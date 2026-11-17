"""Collect metrics for every load job in this workflow run and post them on the issue.

Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, GITHUB_SERVER_URL,
     ISSUE_NUMBER, EXPECTED, RUNS_ON, REQUESTED_AT, RESULTS_DIR (downloaded result-* artifacts),
     WATCHDOG_FILE (optional).
Writes metrics.json and report.md; posts report.md as an issue comment.
"""
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

import github

LOAD_JOB = re.compile(r"^load #(\d+)$")
SETUP_STEP = "Set up job"
WORKLOAD_STEP = "Run workload"
MAX_COMMENT = 60000
PASS_LABEL, FAIL_LABEL = "load-test: passed", "load-test: failed"


def is_load_job(job):
    return bool(LOAD_JOB.match(job.get("name", "")))


def ts(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def span(start, end):
    a, b = ts(start), ts(end)
    return round(b - a, 1) if a is not None and b is not None and b >= a else None


def step(job, name):
    return next((s for s in job.get("steps") or [] if s.get("name") == name), None)


def read_result(results_dir, index):
    """Values the job wrote itself (result-<i>/meta.txt as key=value, output.log)."""
    base = os.path.join(results_dir, f"result-{index}")
    meta = {}
    try:
        with open(os.path.join(base, "meta.txt")) as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    meta[k] = v
    except FileNotFoundError:
        return None
    try:
        with open(os.path.join(base, "output.log"), errors="replace") as f:
            meta["output_tail"] = f.read()[-2000:]
    except FileNotFoundError:
        pass
    return meta


def collect(jobs, results_dir, annotations_for):
    runs = []
    for job in jobs:
        m = LOAD_JOB.match(job.get("name", ""))
        if not m:
            continue
        index = int(m.group(1))
        started = bool(job.get("runner_name"))
        setup, workload = step(job, SETUP_STEP), step(job, WORKLOAD_STEP)
        result = read_result(results_dir, index) or {}
        errors = []
        if job.get("conclusion") not in ("success", None):
            errors += [a for a in annotations_for(job) if a]
            failed = [s["name"] for s in job.get("steps") or [] if s.get("conclusion") == "failure"]
            if failed:
                errors.append("Failed step: " + ", ".join(failed))
            if result.get("exit_code") not in (None, "0"):
                errors.append(f"Workload exited with code {result['exit_code']}")
            if not started:
                errors.append("Never got a runner")
        runs.append({
            "index": index,
            "job_id": job.get("id"),
            "url": job.get("html_url"),
            "status": job.get("status"),
            "conclusion": job.get("conclusion"),
            "started": started,
            "runner_name": job.get("runner_name") or None,
            "runner_group": job.get("runner_group_name") or None,
            "labels": job.get("labels") or [],
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at") if started else None,
            "completed_at": job.get("completed_at"),
            "queue_seconds": span(job.get("created_at"), job.get("started_at")) if started else None,
            "setup_seconds": span(setup.get("started_at"), setup.get("completed_at")) if setup and started else None,
            "workload_seconds": span(workload.get("started_at"), workload.get("completed_at")) if workload and started else None,
            "duration_seconds": span(job.get("started_at"), job.get("completed_at")) if started else None,
            "host": result.get("host"),
            "os": result.get("os"),
            "arch": result.get("arch"),
            "cpus": result.get("cpus"),
            "private_ips": [ip for ip in (result.get("private_ips") or "").split(",") if ip],
            "public_ip": result.get("public_ip") or None,
            "exit_code": result.get("exit_code"),
            "errors": list(dict.fromkeys(errors)),
            "output_tail": result.get("output_tail") if job.get("conclusion") != "success" else None,
        })
    return sorted(runs, key=lambda r: r["index"])


def stats(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None

    def pct(p):
        return values[min(len(values) - 1, max(0, math.ceil(p / 100 * len(values)) - 1))]

    return {
        "min": values[0],
        "avg": round(sum(values) / len(values), 1),
        "p50": pct(50),
        "p95": pct(95),
        "max": values[-1],
    }


def summarize(runs, expected, requested_at, watchdog):
    by = defaultdict(int)
    for r in runs:
        by[r["conclusion"] or r["status"] or "unknown"] += 1
    succeeded = by.get("success", 0)
    starts = [ts(r["started_at"]) for r in runs if r["started_at"]]
    ends = [ts(r["completed_at"]) for r in runs if r["completed_at"] and r["started"]]
    created = [ts(r["created_at"]) for r in runs if r["created_at"]]
    errors = defaultdict(list)
    for r in runs:
        for e in r["errors"]:
            errors[e].append(r["index"])
    return {
        "expected": expected,
        "reported": len(runs),
        "succeeded": succeeded,
        "failed": by.get("failure", 0),
        "cancelled": by.get("cancelled", 0),
        "timed_out": by.get("timed_out", 0),
        "never_started": sum(1 for r in runs if not r["started"]),
        "conclusions": dict(by),
        "success_rate": round(100 * succeeded / expected, 1) if expected else 0,
        "distinct_runners": len({r["runner_name"] for r in runs if r["runner_name"]}),
        "distinct_public_ips": len({r["public_ip"] for r in runs if r["public_ip"]}),
        "max_concurrency": max_concurrency(runs),
        "first_start_after_request_seconds": round(min(starts) - ts(requested_at), 1) if starts and requested_at else None,
        "all_started_after_request_seconds": round(max(starts) - ts(requested_at), 1) if starts and requested_at and len(starts) == expected else None,
        "wall_clock_seconds": round(max(ends) - (ts(requested_at) or min(created)), 1) if ends and created else None,
        "queue_seconds": stats(r["queue_seconds"] for r in runs),
        "setup_seconds": stats(r["setup_seconds"] for r in runs),
        "workload_seconds": stats(r["workload_seconds"] for r in runs),
        "duration_seconds": stats(r["duration_seconds"] for r in runs),
        "errors": [{"message": k, "runs": v} for k, v in sorted(errors.items(), key=lambda kv: -len(kv[1]))],
        "queue_timeout": watchdog,
    }


def max_concurrency(runs):
    events = []
    for r in runs:
        a, b = ts(r["started_at"]), ts(r["completed_at"])
        if a is not None and b is not None:
            events += [(a, 1), (b, -1)]
    best = cur = 0
    for _, delta in sorted(events, key=lambda e: (e[0], e[1])):
        cur += delta
        best = max(best, cur)
    return best


def fmt(seconds):
    if seconds is None:
        return "–"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(round(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def render(summary, runs, runs_on, run_url, include_runs=True, include_ips=True):
    passed = summary["succeeded"] == summary["expected"]
    label = ", ".join(f"`{l}`" for l in (runs_on if isinstance(runs_on, list) else [runs_on]))
    verdict = "PASSED" if passed else "FAILED"
    out = [
        f"## Runner load test {verdict}: {summary['succeeded']}/{summary['expected']} runs succeeded on {label}",
        "",
        f"[Workflow run]({run_url})",
        "",
    ]
    if summary["queue_timeout"]:
        q = summary["queue_timeout"]
        out += [f"> **Queue timeout:** {len(q.get('waiting', []))} jobs had no runner after "
                f"{q.get('queue_timeout_minutes')} min, so the run was cancelled.", ""]
    out += [
        "| Metric | Value |",
        "|---|---|",
        f"| Requested runs | {summary['expected']} |",
        f"| Succeeded | {summary['succeeded']} |",
        f"| Failed | {summary['failed']} |",
        f"| Cancelled | {summary['cancelled']} |",
        f"| Timed out | {summary['timed_out']} |",
        f"| Never got a runner | {summary['never_started']} |",
        f"| Success rate | {summary['success_rate']}% |",
        f"| Distinct runners used | {summary['distinct_runners']} |",
        f"| Distinct public IPs | {summary['distinct_public_ips']} |",
        f"| Peak concurrent jobs | {summary['max_concurrency']} |",
        f"| First job started (after request) | {fmt(summary['first_start_after_request_seconds'])} |",
        f"| All jobs started (after request) | {fmt(summary['all_started_after_request_seconds'])} |",
        f"| Total wall clock (request to last job done) | {fmt(summary['wall_clock_seconds'])} |",
        "",
        "### Timings",
        "",
        "| Phase | Min | Avg | p50 | p95 | Max |",
        "|---|---|---|---|---|---|",
    ]
    for key, name in [
        ("queue_seconds", "Startup: queued until a runner picked it up"),
        ("setup_seconds", "Startup: runner job setup"),
        ("workload_seconds", "Workload"),
        ("duration_seconds", "Job total (pickup to complete)"),
    ]:
        s = summary[key]
        out.append(f"| {name} | " + (" | ".join(fmt(s[k]) for k in ("min", "avg", "p50", "p95", "max")) if s else "– | – | – | – | –") + " |")
    out.append("")
    if summary["errors"]:
        out += ["### Errors", "", "| Error | Count | Runs |", "|---|---|---|"]
        for e in summary["errors"][:30]:
            idx = ", ".join(f"#{i}" for i in e["runs"][:20]) + (" …" if len(e["runs"]) > 20 else "")
            out.append(f"| {cell(e['message'][:300])} | {len(e['runs'])} | {idx} |")
        out.append("")
        tail = next((r for r in runs if r["output_tail"]), None)
        if tail:
            out += [f"<details><summary>Output of failed run #{tail['index']} (last lines)</summary>", "",
                    "```", tail["output_tail"][-1500:].replace("```", "'''"), "```", "</details>", ""]
    if include_ips:
        collapse = len(runs) > 20
        if collapse:
            out += ["<details><summary>IP addresses by run</summary>", ""]
        else:
            out += ["### IP addresses by run", ""]
        out += ["| Run | Runner | Private IPs | Public IP |", "|---|---|---|---|"]
        for r in runs:
            out.append(f"| [#{r['index']}]({r['url']}) | {cell(r['runner_name'] or '–')} "
                       f"| {cell(', '.join(r['private_ips']) or '–')} | {cell(r['public_ip'] or '–')} |")
        out += ["", "</details>", ""] if collapse else [""]
    if include_runs:
        out += ["<details><summary>Per-run details</summary>", "",
                "| Run | Result | Runner | Host | Queued | Setup | Workload | Total |",
                "|---|---|---|---|---|---|---|---|"]
        for r in runs:
            result = r["conclusion"] or r["status"]
            out.append(
                f"| [#{r['index']}]({r['url']}) | {result} | {cell(r['runner_name'] or '–')} | {cell(r['host'] or '–')} "
                f"| {fmt(r['queue_seconds'])} | {fmt(r['setup_seconds'])} | {fmt(r['workload_seconds'])} | {fmt(r['duration_seconds'])} |"
            )
        out += ["", "</details>", ""]
    if not (include_runs and include_ips):
        out += ["Some per-run tables were left out to fit GitHub's comment limit; all of it is in the "
                "`load-test-metrics` artifact of the workflow run.", ""]
    return "\n".join(out)


def annotations_fetcher(repo):
    def fetch(job):
        try:
            items = github.request("GET", f"/repos/{repo}/check-runs/{job['id']}/annotations?per_page=50") or []
        except RuntimeError as e:
            print(f"::warning::Could not read annotations for {job.get('name')}: {e}")
            return []
        return [a.get("message", "").strip() for a in items if a.get("annotation_level") == "failure"]
    return fetch


def set_labels(issue, passed):
    repo = github.REPO
    add, remove = (PASS_LABEL, FAIL_LABEL) if passed else (FAIL_LABEL, PASS_LABEL)
    try:
        github.request("POST", f"/repos/{repo}/issues/{issue}/labels", {"labels": [add]})
        github.request("DELETE", f"/repos/{repo}/issues/{issue}/labels/{remove.replace(' ', '%20').replace(':', '%3A')}")
    except RuntimeError as e:
        if "404" not in str(e):
            print(f"::warning::Could not update labels: {e}")


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}"
    expected = int(os.environ["EXPECTED"])
    runs_on = json.loads(os.environ["RUNS_ON"])
    watchdog = None
    wd = os.environ.get("WATCHDOG_FILE")
    if wd and os.path.exists(wd):
        with open(wd) as f:
            watchdog = json.load(f)

    runs = collect(github.run_jobs(run_id, attempt), os.environ.get("RESULTS_DIR", "results"), annotations_fetcher(repo))
    summary = summarize(runs, expected, os.environ.get("REQUESTED_AT"), watchdog)
    with open("metrics.json", "w") as f:
        json.dump({"run_url": run_url, "runs_on": runs_on, "summary": summary, "runs": runs}, f, indent=2)

    for include_runs, include_ips in ((True, True), (False, True), (False, False)):
        body = render(summary, runs, runs_on, run_url, include_runs=include_runs, include_ips=include_ips)
        if len(body) <= MAX_COMMENT:
            break
    with open("report.md", "w") as f:
        f.write(body)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(body + "\n")

    issue = os.environ["ISSUE_NUMBER"]
    github.comment(issue, body)
    set_labels(issue, summary["succeeded"] == expected)
    print(f"Posted report to issue #{issue}: {summary['succeeded']}/{expected} succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
