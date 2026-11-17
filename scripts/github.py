"""Minimal GitHub REST client (stdlib only) for the load-test workflow."""
import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPO = os.environ.get("GITHUB_REPOSITORY", "")


def request(method, path, body=None, retries=3):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url} -> {e.code}: {e.read().decode(errors='replace')[:500]}") from e
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def paginate(path, key):
    items, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        data = request("GET", f"{path}{sep}per_page=100&page={page}")
        batch = data[key] if isinstance(data, dict) else data
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def comment(issue, body):
    return request("POST", f"/repos/{REPO}/issues/{issue}/comments", {"body": body})


def run_jobs(run_id, attempt):
    return paginate(f"/repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")


def write_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{name}={value}")
        return
    with open(path, "a") as f:
        if "\n" in str(value):
            delim = f"EOF_{os.urandom(8).hex()}"
            f.write(f"{name}<<{delim}\n{value}\n{delim}\n")
        else:
            f.write(f"{name}={value}\n")
