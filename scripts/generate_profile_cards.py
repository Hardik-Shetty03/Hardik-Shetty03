#!/usr/bin/env python3
'''Generate self-hosted GitHub profile statistic cards.

The generated SVG files are committed into the profile repository by GitHub
Actions. This avoids relying on a public statistics-card deployment at page
render time.
'''

from __future__ import annotations

import html
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
OUTPUT_DIR = Path("assets")
STATS_PATH = OUTPUT_DIR / "github-stats.svg"
LANGUAGES_PATH = OUTPUT_DIR / "top-languages.svg"

COLORS = {
    "background": "#19191D",
    "background_dark": "#0D0D0F",
    "border": "#34343A",
    "orange": "#FF003C",
    "lime": "#FF5E7E",
    "ivory": "#F2EBDD",
    "muted": "#929097",
}

LANGUAGE_COLORS = [
    "#FF003C",
    "#FF5E7E",
    "#F2EBDD",
    "#929097",
    "#B3002B",
    "#610014",
    "#C7C2B8",
    "#68666D",
]


def github_request(path: str, token: str) -> tuple[Any, dict[str, str]]:
    request = Request(
        f"{API_ROOT}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "profile-card-generator",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
            headers = {key.lower(): value for key, value in response.headers.items()}
            return body, headers
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Unable to reach the GitHub API: {exc.reason}") from exc


def fetch_repositories(username: str, token: str) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    page = 1
    while True:
        data, _ = github_request(
            f"/users/{username}/repos?type=owner&sort=updated&per_page=100&page={page}",
            token,
        )
        if not isinstance(data, list):
            raise RuntimeError("Unexpected repository response from GitHub API.")
        repositories.extend(data)
        if len(data) < 100:
            break
        page += 1
    return repositories


def fetch_language_totals(
    repositories: list[dict[str, Any]], token: str
) -> Counter[str]:
    totals: Counter[str] = Counter()
    for repository in repositories:
        if repository.get("fork") or repository.get("archived"):
            continue
        full_name = repository.get("full_name")
        if not full_name:
            continue
        languages, _ = github_request(f"/repos/{full_name}/languages", token)
        if not isinstance(languages, dict):
            continue
        for language, byte_count in languages.items():
            if isinstance(byte_count, int) and byte_count > 0:
                totals[str(language)] += byte_count
    return totals


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def format_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def svg_shell(title: str, body: str, width: int = 560, height: int = 220) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
  <style>
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }}
    .sans {{ font-family: Arial, Helvetica, sans-serif; }}
  </style>
  <rect x="1" y="1" width="{width - 2}" height="{height - 2}" fill="{COLORS['background']}" stroke="{COLORS['border']}" stroke-width="2"/>
  <rect x="1" y="1" width="8" height="{height - 2}" fill="{COLORS['orange']}"/>
  <rect x="{width - 9}" y="1" width="8" height="{height - 2}" fill="{COLORS['lime']}"/>
  <text x="28" y="35" class="mono" fill="{COLORS['orange']}" font-size="14" font-weight="800" letter-spacing="2">{html.escape(title)}</text>
  <line x1="28" y1="49" x2="{width - 28}" y2="49" stroke="{COLORS['border']}" stroke-width="2" stroke-dasharray="10 8"/>
  {body}
</svg>'''


def create_stats_svg(user: dict[str, Any], repositories: list[dict[str, Any]]) -> str:
    owned = [repo for repo in repositories if not repo.get("fork")]
    total_stars = sum(int(repo.get("stargazers_count", 0)) for repo in owned)
    total_forks = sum(int(repo.get("forks_count", 0)) for repo in owned)
    stats = [
        ("PUBLIC REPOS", int(user.get("public_repos", len(repositories)))),
        ("FOLLOWERS", int(user.get("followers", 0))),
        ("TOTAL STARS", total_stars),
        ("TOTAL FORKS", total_forks),
    ]

    blocks: list[str] = []
    positions = [(28, 72), (292, 72), (28, 142), (292, 142)]
    for (label, value), (x, y) in zip(stats, positions):
        accent = COLORS["lime"] if label in {"FOLLOWERS", "TOTAL FORKS"} else COLORS["orange"]
        blocks.append(
            f'''<g transform="translate({x} {y})">
    <rect width="240" height="52" fill="{COLORS['background_dark']}" stroke="{COLORS['border']}"/>
    <text x="15" y="20" class="mono" fill="{COLORS['muted']}" font-size="10" letter-spacing="1.5">{html.escape(label)}</text>
    <text x="15" y="42" class="sans" fill="{COLORS['ivory']}" font-size="20" font-weight="700">{format_number(value)}</text>
    <rect x="224" y="10" width="4" height="32" fill="{accent}"/>
  </g>'''
        )
    return svg_shell("SYSTEM ACTIVITY", "\n  ".join(blocks))


def create_languages_svg(language_totals: Counter[str]) -> str:
    total_bytes = sum(language_totals.values())
    if total_bytes <= 0:
        body = f'''<text x="28" y="102" class="sans" fill="{COLORS['ivory']}" font-size="18" font-weight="700">No public language data yet.</text>
  <text x="28" y="133" class="mono" fill="{COLORS['muted']}" font-size="11" letter-spacing="1.2">THE CARD WILL UPDATE AUTOMATICALLY.</text>'''
        return svg_shell("PUBLIC REPO LANGUAGES", body)

    top_languages = language_totals.most_common(5)
    body_parts: list[str] = []
    for index, (language, byte_count) in enumerate(top_languages):
        percentage = byte_count / total_bytes * 100
        y = 74 + index * 27
        bar_width = max(3.0, min(240.0, percentage / 100 * 240.0))
        color = LANGUAGE_COLORS[index % len(LANGUAGE_COLORS)]
        body_parts.append(
            f'''<text x="28" y="{y + 10}" class="mono" fill="{COLORS['ivory']}" font-size="11" font-weight="700">{html.escape(language)}</text>
  <rect x="170" y="{y}" width="240" height="12" fill="{COLORS['background_dark']}" stroke="{COLORS['border']}"/>
  <rect x="170" y="{y}" width="{bar_width:.1f}" height="12" fill="{color}"/>
  <text x="425" y="{y + 10}" class="mono" fill="{COLORS['muted']}" font-size="10">{percentage:.1f}%</text>'''
        )
    return svg_shell("PUBLIC REPO LANGUAGES", "\n  ".join(body_parts))


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    username = os.environ.get("GITHUB_USERNAME", "").strip()
    if not token:
        print("GITHUB_TOKEN is required.", file=sys.stderr)
        return 2
    if not username:
        print("GITHUB_USERNAME is required.", file=sys.stderr)
        return 2

    user, _ = github_request(f"/users/{username}", token)
    if not isinstance(user, dict):
        raise RuntimeError("Unexpected user response from GitHub API.")

    repositories = fetch_repositories(username, token)
    language_totals = fetch_language_totals(repositories, token)

    atomic_write(STATS_PATH, create_stats_svg(user, repositories))
    atomic_write(LANGUAGES_PATH, create_languages_svg(language_totals))
    print(f"Updated {STATS_PATH} and {LANGUAGES_PATH}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Profile card generation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
