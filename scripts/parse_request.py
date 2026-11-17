"""Parse a load-test issue, validate it and announce the run on the issue.

Env: GITHUB_TOKEN, GITHUB_REPOSITORY, ISSUE_NUMBER, RUN_URL,
     COMMENT_ASSOCIATION / COMMENT_CREATED_AT (set when triggered by /rerun),
     TRUSTED=true (set for workflow_dispatch).
Outputs: runs_on, matrix, count, timeout, queue_timeout, command, requested_at.
"""
import json
import os
import re
import sys

import github

MAX_RUNS = 256
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
EMPTY = {"", "_no response_", "none"}


class RequestError(ValueError):
    pass


def sections(body):
    """Map issue-form headings ('### Label') to their raw values."""
    out, current, lines = {}, None, []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                out[current] = "\n".join(lines).strip()
            current, lines = m.group(1).strip().lower(), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        out[current] = "\n".join(lines).strip()
    return out


def strip_fence(text):
    m = re.match(r"^```[^\n]*\n(.*?)\n?```$", text.strip(), re.S)
    return m.group(1) if m else text


def int_field(fields, name, default, lo, hi):
    raw = fields.get(name, "").strip()
    if raw.lower() in EMPTY:
        if default is None:
            raise RequestError(f"**{name.capitalize()}** is required.")
        return default
    if not re.fullmatch(r"\d+", raw):
        raise RequestError(f"**{name.capitalize()}** must be a whole number, got `{raw}`.")
    value = int(raw)
    if not lo <= value <= hi:
        raise RequestError(f"**{name.capitalize()}** must be between {lo} and {hi}, got {value}.")
    return value


def parse(body):
    fields = sections(body)
    raw_label = fields.get("runner label", "").strip().strip("`")
    labels = [l.strip().strip("`") for l in raw_label.split(",") if l.strip()]
    if not labels or raw_label.lower() in EMPTY:
        raise RequestError("**Runner label** is required.")
    for label in labels:
        if not re.fullmatch(r"[A-Za-z0-9._:/@+-]+", label):
            raise RequestError(f"Runner label `{label}` has characters that are not allowed.")
    command = strip_fence(fields.get("command", "")).strip()
    if command.lower() in EMPTY:
        command = ""
    return {
        "labels": labels,
        "count": int_field(fields, "concurrent runs", None, 1, MAX_RUNS),
        "timeout": int_field(fields, "job timeout (minutes)", 30, 1, 360),
        "queue_timeout": int_field(fields, "queue timeout (minutes)", 60, 0, 1440),
        "command": command,
    }


def main():
    issue_number = os.environ["ISSUE_NUMBER"]
    repo = os.environ["GITHUB_REPOSITORY"]
    run_url = os.environ.get("RUN_URL", "")
    issue = github.request("GET", f"/repos/{repo}/issues/{issue_number}")

    association = os.environ.get("COMMENT_ASSOCIATION") or issue.get("author_association", "")
    if os.environ.get("TRUSTED") != "true" and association not in TRUSTED_ASSOCIATIONS:
        github.comment(issue_number, "Load tests can only be started by repository collaborators.")
        print(f"::error::Author association {association} is not allowed to start load tests")
        return 1

    try:
        req = parse(issue.get("body"))
    except RequestError as e:
        github.comment(issue_number, f"Could not start the load test: {e}\n\nEdit the issue and comment `/rerun`.")
        print(f"::error::{e}")
        return 1

    runs_on = req["labels"][0] if len(req["labels"]) == 1 else req["labels"]
    github.write_output("runs_on", json.dumps(runs_on))
    github.write_output("matrix", json.dumps(list(range(1, req["count"] + 1))))
    github.write_output("count", req["count"])
    github.write_output("timeout", req["timeout"])
    github.write_output("queue_timeout", req["queue_timeout"])
    github.write_output("command", req["command"])
    github.write_output("requested_at", os.environ.get("COMMENT_CREATED_AT") or issue["created_at"])

    label_text = ", ".join(f"`{l}`" for l in req["labels"])
    queue = f"{req['queue_timeout']} min" if req["queue_timeout"] else "none"
    github.comment(
        issue_number,
        f"Started **{req['count']}** concurrent runs on {label_text} "
        f"(job timeout {req['timeout']} min, queue timeout {queue}).\n\n"
        f"[Workflow run]({run_url}). The metrics report will be posted here when all runs finish.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
