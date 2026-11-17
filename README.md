# runner-load-tester

Load-test a GitHub Actions runner label from an issue.

1. **New issue → Runner load test.** Fill in the runner label (`runs-on`, comma-separate several labels) and the number of concurrent runs (1-256). Command, job timeout and queue timeout are optional.
2. **Submit.** The `Runner load test` workflow comments on the issue and starts N `load #i` jobs at once (`max-parallel: N`) on that label.
3. **Report.** When every job has finished, the `report` job reads the run's job/step timings and annotations from the Actions API plus what each job wrote about itself, posts the report on the issue, and labels it `load-test: passed` or `load-test: failed`. The raw data is in the run's `load-test-metrics` artifact (`metrics.json`, `report.md`).

Comment `/rerun` on the issue to run it again with the issue's current values (edit the issue first to change them). From the Actions tab, **Run workflow** with an issue number does the same.

## What the report contains

| Metric | Source |
|---|---|
| Succeeded / failed / cancelled / timed out / never got a runner, success rate | job `conclusion` and `runner_name` |
| Startup: time queued until a runner picked the job up | job `created_at` → `started_at` |
| Startup: runner job setup | `Set up job` step duration |
| Workload time, job total | `Run workload` step, job `started_at` → `completed_at` |
| First / all jobs started after the request, wall clock (request to last job done), peak concurrency | job timestamps vs. the request (new issue, `/rerun` comment, or run start for dispatch/reopen) |
| Distinct runners, per-run runner, host, OS/arch | job `runner_name`, values the job records |
| Private IPs and public (egress) IP of each run, distinct public IPs | recorded on the runner before the workload (`hostname -I` / `ip` / `ifconfig`; public IP from api.ipify.org, checkip.amazonaws.com or ifconfig.me) |
| Errors grouped by message, with the runs they hit | failure annotations, failed steps, workload exit code, last output lines |

Min, avg, p50, p95 and max are given for each timing.

## Options

| Field | Default | Notes |
|---|---|---|
| Command | prints runner info, `sleep 10` | Run with `bash -eo pipefail` on each runner; non-zero exit fails that run. |
| Job timeout (minutes) | 30 | `timeout-minutes` of each load job (1-360). |
| Queue timeout (minutes) | 60 | Max 340. If any job still has no runner after this long (e.g. a mistyped label), the run is cancelled and the report says so. `0` waits indefinitely (GitHub's own limit is 24h). |

## Setup

- The issue form applies the `runner-load-test` label; create it in the repo if it does not exist (issues whose body starts with the form's `### Runner label` heading are picked up either way).
- `plan`, `watchdog` and `report` run on `ubuntu-latest`. Set the repo variable `CONTROL_RUNNER` to run them elsewhere; that runner needs `python3`.
- Only the repo owner, org members and collaborators can start a test from an issue or `/rerun`, because the command runs on your runners.
- Each test uses N + 3 jobs; GitHub caps a matrix at 256 jobs, hence the 256-run limit.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Scripts live in `scripts/` and use only the Python standard library.
