#!/usr/bin/env python3
"""Fetch real GitHub stats for Rohith-E-S and update README.md between
the <!--github-stats--> markers. Runs in GitHub Actions daily."""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

LOGIN = os.environ.get("GITHUB_LOGIN", "Rohith-E-S")
TOKEN = os.environ.get("GITHUB_TOKEN")
API = "https://api.github.com/graphql"


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def contributions(login):
    q = """
    query($login: String!) {
      user(login: $login) {
        createdAt
        contributionsCollection {
          totalCommitContributions
          totalPullRequestContributions
          totalIssueContributions
          totalPullRequestReviewContributions
          restrictedContributionsCount
        }
      }
    }"""
    return gql(q, {"login": login})["user"]


def repos(login):
    q = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositories(first: 100, after: $cursor, ownerAffiliations: OWNER,
                     isFork: false, privacy: PUBLIC) {
          pageInfo { hasNextPage endCursor }
          nodes { name primaryLanguage { name } }
        }
      }
    }"""
    out, cursor = [], None
    while True:
        d = gql(q, {"login": login, "cursor": cursor})["user"]["repositories"]
        out += [n for n in d["nodes"]]
        if not d["pageInfo"]["hasNextPage"]:
            return out
        cursor = d["pageInfo"]["endCursor"]


def lang_counts(nodes):
    counts = {}
    for n in nodes:
        lang = (n.get("primaryLanguage") or {}).get("name")
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def lines_of_code(login, names):
    """Sum additions/deletions from each owned non-fork public repo's
    default branch, paginating through full commit history."""
    q = """
    query($owner: String!, $name: String!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef {
          target { ... on Commit {
            history(first: 100, after: $cursor) {
              pageInfo { hasNextPage endCursor }
              edges { node {
                additions
                deletions
                author { user { login } }
              }}
            }
          }}
        }
      }
    }"""
    adds = dels = 0
    for name in names:
        cursor = None
        while True:
            try:
                d = gql(q, {"owner": login, "name": name, "cursor": cursor})
                ref = d["repository"]["defaultBranchRef"]
                if not ref:
                    break
                hist = ref["target"]["history"]
            except Exception:
                break
            for e in hist["edges"]:
                n = e["node"]
                author = ((n.get("author") or {}).get("user") or {}).get("login")
                if author == login:
                    adds += n["additions"]
                    dels += n["deletions"]
            if not hist["pageInfo"]["hasNextPage"]:
                break
            cursor = hist["pageInfo"]["endCursor"]
    return adds, dels


def fmt_int(n):
    return f"{n:,}"


def main():
    user = contributions(LOGIN)
    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - created).days
    cc = user["contributionsCollection"]
    r = repos(LOGIN)
    names = [n["name"] for n in r]
    langs = lang_counts(r)
    adds, dels = lines_of_code(LOGIN, names)

    lines = [
        f"Commits (public): {fmt_int(cc['totalCommitContributions'])}",
        f"Commits (private): {fmt_int(cc['restrictedContributionsCount'])}",
        f"Pull Requests: {fmt_int(cc['totalPullRequestContributions'])}",
        f"Repos: {fmt_int(len(r))}",
        f"Lines of Code: {{ {fmt_int(adds)}++, {fmt_int(dels)}-- }}",
        f"Age: {fmt_int(days)} days",
    ]
    stats_block = "\n".join(lines)

    readme = open("README.md").read()
    pattern = re.compile(
        r"(<!--github-stats-->\n)(.*?)(\n<!--/github-stats-->)", re.S)
    if not pattern.search(readme):
        sys.exit("README.md missing <!--github-stats--> markers")
    updated = pattern.sub(
        lambda m: m.group(1) + stats_block + m.group(3), readme)
    open("README.md", "w").write(updated)
    print(stats_block)
    print(f"\nLangs: {langs}")


if __name__ == "__main__":
    main()
