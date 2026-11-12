"""Fetch merged pull requests from a GitHub repo and write them to a CSV.

Each row has the PR plus details about its author and the user who merged it.

Usage:
  python3 fetch_merged_prs.py [owner/repo] [--out merged_prs.csv] [--limit N]
"""
import argparse
import csv
import os
import subprocess
import sys
from datetime import datetime

import requests

API = "https://api.github.com"
DEFAULT_REPO = "willbetts/dx-mock"  # repo used when none is passed
USER_FIELDS = ["login", "id", "name", "email", "company", "location", "type", "created_at", "html_url"]
PR_FIELDS = ["pr_number", "title", "additions", "deletions", "created_at", "merged_at",
             "time_to_merge_hours", "time_to_merge", "html_url"]


def parse_ts(s):
    # GitHub timestamps are ISO 8601 in UTC, e.g. 2026-11-11T19:09:45Z
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def token():
    """GITHUB_TOKEN env var first, then the gh CLI. Never hardcoded."""
    if t := os.environ.get("GITHUB_TOKEN"):
        return t
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def make_session():
    s = requests.Session()
    s.headers["Accept"] = "application/vnd.github+json"
    if t := token():
        s.headers["Authorization"] = f"Bearer {t}"
    return s


def get(session, url, params=None):
    r = session.get(url, params=params, timeout=30)
    if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
        sys.exit("Rate limited by GitHub; try again after X-RateLimit-Reset or use a token.")
    r.raise_for_status()
    return r


def merged_prs(session, repo, limit=None):
    """Yield merged PRs. The list endpoint has no 'merged' filter, so fetch
    closed PRs and keep those with merged_at set (closed-unmerged have None)."""
    url, params = f"{API}/repos/{repo}/pulls", {"state": "closed", "per_page": 100}
    count = 0
    while url:
        r = get(session, url, params)
        for pr in r.json():
            if pr["merged_at"]:
                yield pr
                count += 1
                if limit and count >= limit:
                    return
        url, params = r.links.get("next", {}).get("url"), None  # follow pagination


def user_details(session, login, cache):
    """Full user profile (name, email, company...). The PR payload only has a
    summary, so look each user up once and cache it."""
    if login not in cache:
        cache[login] = get(session, f"{API}/users/{login}").json()
    return cache[login]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=DEFAULT_REPO, help=f"owner/repo (default: {DEFAULT_REPO})")
    ap.add_argument("--out", default="merged_prs.csv")
    ap.add_argument("--limit", type=int, help="stop after N merged PRs")
    args = ap.parse_args()

    session, cache, rows = make_session(), {}, []
    for pr in merged_prs(session, args.repo, args.limit):
        # merged_by is only on the single-PR endpoint, not the list endpoint.
        detail = get(session, pr["url"]).json()
        merger = detail.get("merged_by") or pr["user"]  # same as author in this exercise
        author = user_details(session, pr["user"]["login"], cache)
        merged_by = user_details(session, merger["login"], cache)

        # additions/deletions are only on the single-PR endpoint (detail).
        delta = parse_ts(pr["merged_at"]) - parse_ts(pr["created_at"])
        row = {
            "pr_number": pr["number"],
            "title": pr["title"],
            "additions": detail["additions"],
            "deletions": detail["deletions"],
            "created_at": pr["created_at"],
            "merged_at": pr["merged_at"],
            "time_to_merge_hours": round(delta.total_seconds() / 3600, 2),
            "time_to_merge": str(delta),  # human-readable, e.g. "2 days, 3:14:05"
            "html_url": pr["html_url"],
        }
        row.update({f"author_{f}": author.get(f) for f in USER_FIELDS})
        row.update({f"merged_by_{f}": merged_by.get(f) for f in USER_FIELDS})
        rows.append(row)

    fields = (PR_FIELDS
              + [f"author_{f}" for f in USER_FIELDS] + [f"merged_by_{f}" for f in USER_FIELDS])
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} merged PRs from {args.repo} to {args.out}")


if __name__ == "__main__":
    main()
