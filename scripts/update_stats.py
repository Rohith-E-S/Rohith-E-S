#!/usr/bin/env python3
"""Fetch real GitHub stats for Rohith-E-S and update README.md between
the <!--github-stats-->, <!--languages-->, <!--featured--> markers and
inline <!--kernel--> version. Runs in GitHub Actions daily.

Featured projects + language bar are ranked by commits in the
last 365 days (public, non-fork repos, authored by LOGIN).
Kernel version is the latest Arch Linux `linux` package (uname -r style),
since the Actions runner is Ubuntu, not the author's Arch machine.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

LOGIN = os.environ.get("GITHUB_LOGIN", "Rohith-E-S")
TOKEN = os.environ.get("GITHUB_TOKEN")
API = "https://api.github.com/graphql"
REST = "https://api.github.com"

# Curated one-line blurbs for known projects (used in Featured table).
# Falls back to the repo's GitHub description for anything not listed here.
DESCRIPTIONS = {
    "adbshare": "Browse Android phone & move files over USB/Wi-Fi via ADB, with FUSE mounts.",
    "radiothing": "Dot-matrix internet radio for Android — thousands of stations, CRT equalizer, no ads.",
    "CivicResolve": "Civic complaint platform — React web + Kotlin Android + Express/MongoDB, real-time updates.",
    "HyperGesture": "Edge gestures for Xiaomi HyperOS so you can keep your favourite launcher.",
    "CircleToSearch-desktop": "Circle to Search for Linux & Windows — drag to select, instant visual search.",
    "DotDesktop": "Keyboard-first editor for Linux `.desktop` entries — fix Wayland/X11 per app, safely.",
    "internet-radio-esp32-bt": "Internet radio from ESP32 — Wi-Fi to Bluetooth speaker, controlled from a mobile app.",
    "p2pnotes": "Browser-based peer-to-peer notes sync with end-to-end encryption.",
    "minimal-mediactrl-caffine": "Minimalist Waybar setup with media controls and Caffeine option.",
    "portfolio": "Personal portfolio site.",
}

# Display label for the language bar (Kotlin is the Android stack).
LANG_LABELS = {
    "Kotlin": "Android (Kotlin)",
}


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


def rest_get(path):
    req = urllib.request.Request(REST + path, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def arch_kernel_version():
    """Return latest Arch Linux kernel in uname -r style, or None on failure.

    Arch json: pkgver '7.2.14.arch1' + pkgrel '1' -> '7.2.14-arch1-1'.
    The runner is Ubuntu, so uname -r there would be misleading.
    """
    try:
        req = urllib.request.Request(
            "https://archlinux.org/packages/core/x86_64/linux/json/",
            headers={"User-Agent": "profile-readme-stats/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        pkgver = data.get("pkgver", "")
        pkgrel = data.get("pkgrel", "1")
        if not pkgver:
            return None
        # '7.2.14.arch1-1' -> '7.2.14-arch1-1'
        version = f"{pkgver}-{pkgrel}".replace(".arch", "-arch")
        return version
    except Exception as e:
        print(f"warn: arch kernel fetch failed: {e}", file=sys.stderr)
        return None


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
          nodes {
            name
            description
            stargazerCount
            url
            primaryLanguage { name }
          }
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


def commit_counts_last_year(login, names, days=365):
    """Count commits by `login` in each repo since `days` ago (REST)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {}
    for name in names:
        total = 0
        page = 1
        while True:
            qs = urllib.parse.urlencode({
                "since": since, "author": login,
                "per_page": 100, "page": page,
            })
            try:
                chunk = rest_get(f"/repos/{login}/{name}/commits?{qs}")
            except Exception as e:
                print(f"warn: commits for {name}: {e}", file=sys.stderr)
                break
            if not isinstance(chunk, list) or not chunk:
                break
            total += len(chunk)
            if len(chunk) < 100:
                break
            page += 1
        counts[name] = total
    return counts


def fmt_int(n):
    return f"{n:,}"


def language_bar(commit_counts, repo_map):
    """Render the ``` bar from commits-last-year grouped by language."""
    by_lang = {}
    for name, count in commit_counts.items():
        lang = ((repo_map.get(name) or {}).get("primaryLanguage") or {}).get("name")
        if lang and count:
            by_lang[lang] = by_lang.get(lang, 0) + count
    total = sum(by_lang.values())
    if not total:
        return "```\nNo commits in the last 365 days.\n```"

    ranked = sorted(by_lang.items(), key=lambda kv: -kv[1])
    top, rest = ranked[:4], ranked[4:]
    rows = [(k, v) for k, v in top]
    if rest:
        rows.append(("Other", sum(v for _, v in rest)))

    lines = []
    for lang, count in rows:
        pct = count / total * 100
        filled = round(pct / 100 * 20)
        filled = max(0, min(20, filled))
        bar = "█" * filled + "░" * (20 - filled)
        label = LANG_LABELS.get(lang, lang)
        lines.append(f"{label:<18} {bar}  {round(pct):>3}%")
    return "```\n" + "\n".join(lines) + "\n```"


def featured_block(repo_nodes, commit_counts, limit=6):
    """Render the Featured table, ranked by commits in last 365 days."""
    ranked = sorted(
        ((commit_counts.get(n["name"], 0), n) for n in repo_nodes),
        key=lambda t: -t[0],
    )
    rows = [(c, n) for c, n in ranked if c > 0][:limit]
    lines = [
        "| Project | What it does | Stack | Last 365d |",
        "|---|---|---|---|",
    ]
    for count, n in rows:
        name = n["name"]
        url = n.get("url") or f"https://github.com/{LOGIN}/{name}"
        desc = DESCRIPTIONS.get(name) or (n.get("description") or "Open source project.")
        desc = desc.replace("|", "/").strip()
        lang = ((n.get("primaryLanguage") or {}).get("name")) or "—"
        stars = n.get("stargazerCount", 0)
        lines.append(f"| [{name}]({url}) | {desc} | {lang} · ★{stars} | {count} commits |")
    return "\n".join(lines)


def replace_block(text, name, new_inner):
    pattern = re.compile(
        rf"(<!--{name}-->\n)(.*?)(\n<!--/{name}-->)", re.S)
    if not pattern.search(text):
        sys.exit(f"README.md missing <!--{name}--> markers")
    return pattern.sub(
        lambda m: m.group(1) + new_inner + m.group(3), text)


def replace_inline(text, name, new_value):
    pattern = re.compile(rf"<!--{name}-->.*?<!--/{name}-->", re.S)
    if not pattern.search(text):
        sys.exit(f"README.md missing <!--{name}--> markers")
    return pattern.sub(f"<!--{name}-->{new_value}<!--/{name}-->", text)


def main():
    user = contributions(LOGIN)
    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - created).days
    cc = user["contributionsCollection"]
    r = repos(LOGIN)
    names = [n["name"] for n in r]
    repo_map = {n["name"]: n for n in r}
    langs = lang_counts(r)
    adds, dels = lines_of_code(LOGIN, names)
    commit_counts = commit_counts_last_year(LOGIN, names)

    lines = [
        f"Commits (public): {fmt_int(cc['totalCommitContributions'])}",
        f"Commits (private): {fmt_int(cc['restrictedContributionsCount'])}",
        f"Pull Requests: {fmt_int(cc['totalPullRequestContributions'])}",
        f"Repos: {fmt_int(len(r))}",
        f"Lines of Code: {{ {fmt_int(adds)}++, {fmt_int(dels)}-- }}",
        f"Age: {fmt_int(days)} days",
    ]
    stats_block = "\n".join(lines)
    lang_block = language_bar(commit_counts, repo_map)
    feat_block = featured_block(r, commit_counts)

    updated = open("README.md").read()
    updated = replace_block(updated, "github-stats", stats_block)
    updated = replace_block(updated, "languages", lang_block)
    updated = replace_block(updated, "featured", feat_block)
    kernel = arch_kernel_version()
    if kernel:
        updated = replace_inline(updated, "kernel", kernel)
        print(f"\nKernel (Arch linux): {kernel}")
    else:
        print("\nKernel: fetch failed, kept existing value", file=sys.stderr)
    open("README.md", "w").write(updated)
    print(stats_block)
    print(f"\nLangs (repo count): {langs}")
    print(f"\n{lang_block}")
    print(f"\n{feat_block}")


if __name__ == "__main__":
    main()
