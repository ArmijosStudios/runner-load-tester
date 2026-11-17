"""Report (and with --cancel, cancel the run) when load jobs are still waiting for a runner after QUEUE_TIMEOUT minutes.

Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, EXPECTED, QUEUE_TIMEOUT.
"""
import json
import os
import sys
import time

import github
from report import is_load_job

POLL_SECONDS = 20


def cancel():
    github.request("POST", f"/repos/{github.REPO}/actions/runs/{os.environ['GITHUB_RUN_ID']}/cancel")
    return 0


def main():
    if sys.argv[1:] == ["--cancel"]:
        return cancel()
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    expected = int(os.environ["EXPECTED"])
    deadline = time.time() + int(os.environ["QUEUE_TIMEOUT"]) * 60
    while True:
        jobs = [j for j in github.run_jobs(run_id, attempt) if is_load_job(j)]
        waiting = [j["name"] for j in jobs if j["status"] in ("queued", "waiting", "pending", "requested")]
        if len(jobs) >= expected and not waiting:
            print(f"All {len(jobs)} load jobs got a runner.")
            return 0
        if time.time() >= deadline:
            break
        print(f"{len(jobs)}/{expected} load jobs created, {len(waiting)} still waiting for a runner")
        time.sleep(POLL_SECONDS)

    note = {"queue_timeout_minutes": int(os.environ["QUEUE_TIMEOUT"]), "waiting": waiting}
    with open("watchdog.json", "w") as f:
        json.dump(note, f)
    print(f"::error::{len(waiting)} load jobs had no runner after {note['queue_timeout_minutes']} min; cancelling the run")
    github.write_output("timed_out", "true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
