"""Write the weekly activity strips and the project table of the profile README."""

import html
import json
import os
import re
import sys
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

OWNER = "stevencarpenter"
# table row order
PROJECTS = [
    "hippo",
    "dotfiles-manager",
    "dotfiles",
    "agents",
    "driving-range",
    "nuv",
    "token-auditor",
    "duckdb.yazi",
    "aws-config-generator",
    "hippo-gui",
]
WEEKS = 52
START_MARK = "<!-- project-activity:start -->"
END_MARK = "<!-- project-activity:end -->"

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "activity"
API_URL = "https://api.github.com/graphql"

WIDTH = 468
LEGEND_WIDTH = 456
COLUMN_TITLE = f"Weekly commits and releases in the last {WEEKS} weeks".replace(" ", "&nbsp;")
MONTHS_ALT = f"Month axis for the last {WEEKS} weeks"
LEGEND_ALT = "Each cell is one week. A stronger green means more commits. An orange dot marks a release."
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"
# shortcut: one text color for the light and dark theme gives about 4.3:1 contrast, use <picture> with two files if 4.5:1 is necessary
TEXT_COLOR = "#727a84"
DOT_COLOR = "#e16f24"
# one style for each commit level
CELL_STYLES = [
    'fill="#8b949e" fill-opacity="0.25"',
    'fill="#2da44e" fill-opacity="0.35"',
    'fill="#2da44e" fill-opacity="0.55"',
    'fill="#2da44e" fill-opacity="0.8"',
    'fill="#2da44e" fill-opacity="1"',
]
# (swatch x, label) for each commit level
LEGEND_SWATCHES = [(104, "0"), (136, "1 to 2"), (192, "3 to 6"), (248, "7 to 14"), (312, "15 or more")]
LEGEND_DOT_X = 396

COMMITS_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      name
      target {
        ... on Commit {
          history(since: $since, first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes { committedDate }
          }
        }
      }
    }
  }
}
"""
TAGS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    refs(refPrefix: "refs/tags/", first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        target {
          __typename
          ... on Commit { committedDate }
          ... on Tag { tagger { date } target { ... on Commit { committedDate } } }
        }
      }
    }
  }
}
"""
RELEASE_QUERY = """
query($owner: String!, $name: String!, $tag: String!) {
  repository(owner: $owner, name: $name) { release(tagName: $tag) { isDraft } }
}
"""


class Project(NamedTuple):
    repo: str
    commits: list[int]  # commit count for each week of the window
    releases: list[int]  # version tag count for each week of the window
    label: str
    link: str


def window_start(now: datetime) -> datetime:
    """Start of week 0: the Sunday 00:00 UTC that is 51 weeks before the week that contains now."""
    today = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # weekday() counts from Monday, so Sunday is 6
    return today - timedelta(days=(today.weekday() + 1) % 7, weeks=WEEKS - 1)


def week_index(moment: datetime, start: datetime) -> int | None:
    index = (moment - start) // timedelta(weeks=1)
    return index if 0 <= index < WEEKS else None


def week_counts(moments: Iterable[datetime], start: datetime) -> list[int]:
    """Number of moments in each week of the window. Moments outside the window are ignored."""
    counts = [0] * WEEKS
    for moment in moments:
        index = week_index(moment, start)
        if index is not None:
            counts[index] += 1
    return counts


def commit_level(count: int) -> int:
    return sum(count >= bound for bound in (1, 3, 7, 15))


def is_version_tag(name: str) -> bool:
    return re.match(r"v?\.?\d+(\.\d+)+", name) is not None


def parse_date(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def tag_date(target: dict[str, Any] | None) -> datetime | None:
    """Tagger date of an annotated tag, else the date of the commit that the tag points to."""
    target = target or {}
    if target.get("__typename") == "Tag":
        text = (target.get("tagger") or {}).get("date") or (target.get("target") or {}).get("committedDate")
    else:
        text = target.get("committedDate")
    return parse_date(text) if text else None


def version_tags(nodes: list[dict[str, Any]]) -> list[tuple[datetime, str]]:
    """(date, name) of each version tag that has a usable date."""
    return [
        (date, node["name"])
        for node in nodes
        if is_version_tag(node["name"]) and (date := tag_date(node["target"])) is not None
    ]


def newest_version_tag(tags: list[tuple[datetime, str]]) -> str | None:
    """Name of the tag with the latest date. The larger name wins when two dates are equal."""
    return max(tags)[1] if tags else None


def latest_version(repo: str, tag: str | None, has_release: bool, branch: str) -> tuple[str, str]:
    """Label and link of the latest version: the newest version tag, else the default branch."""
    base = f"https://github.com/{OWNER}/{repo}"
    if tag is None:
        return branch, f"{base}/tree/{quote(branch)}"
    return tag, f"{base}/{'releases/tag' if has_release else 'tree'}/{quote(tag)}"


def replace_block(readme: str, block: str) -> str:
    start = readme.find(START_MARK)
    end = readme.find(END_MARK)
    if start < 0 or end < start:
        raise ValueError(f"README must contain {START_MARK} before {END_MARK}")
    return f"{readme[:start]}{START_MARK}\n{block}\n{readme[end:]}"


def svg(width: int, height: int, label: str, body: list[str]) -> str:
    head = (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{html.escape(label)}">'
    )
    return "\n".join([head, *body, "</svg>"]) + "\n"


def cell(x: int, y: int, level: int) -> str:
    return f'<rect x="{x}" y="{y}" width="7" height="13" rx="2" {CELL_STYLES[level]}/>'


def dot(cx: float, cy: float, r: float) -> str:
    return f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="{DOT_COLOR}"/>'


def text(x: int, label: str) -> str:
    return f'<text x="{x}" y="11" font-size="11" font-family="{FONT}" fill="{TEXT_COLOR}">{label}</text>'


def strip_svg(repo: str, commits: list[int], releases: list[int]) -> str:
    body = [cell(i * 9, 10, commit_level(count)) for i, count in enumerate(commits)]
    body += [dot(i * 9 + 3.5, 4.5, min(2 + 0.35 * count, 4)) for i, count in enumerate(releases) if count]
    return svg(WIDTH, 24, f"{repo}: weekly commits and releases, last {WEEKS} weeks", body)


def months_svg(start: datetime) -> str:
    months = [(start + timedelta(weeks=i)).month for i in range(WEEKS)]
    # a label in the last 2 weeks moves left, so that the image edge does not cut it
    body = [
        text(min(i * 9, WIDTH - 22), MONTHS[months[i] - 1]) for i in range(1, WEEKS) if months[i] != months[i - 1]
    ]
    return svg(WIDTH, 14, MONTHS_ALT, body)


def legend_svg() -> str:
    body = [text(0, "Weekly commits:")]
    for level, (x, label) in enumerate(LEGEND_SWATCHES):
        body += [cell(x, 0, level), text(x + 11, label)]
    body += [dot(LEGEND_DOT_X + 3.5, 7, 3.5), text(LEGEND_DOT_X + 11, "release")]
    return svg(LEGEND_WIDTH, 14, LEGEND_ALT, body)


def readme_block(projects: list[Project]) -> str:
    lines = [
        # GitHub shrinks an image in a table cell to fit a narrow screen, but not text with no break point.
        # The title keeps the column wide enough to read the strips on a phone.
        f"| Project | {COLUMN_TITLE}<br>"
        f'<img src="assets/activity/months.svg" width="{WIDTH}" height="14" alt="{MONTHS_ALT}">'
        " | Latest version |",
        "| --- | --- | --- |",
    ]
    for project in projects:
        repo = project.repo
        commits, releases = sum(project.commits), sum(project.releases)
        alt = (
            f"{repo}: {commits} commit{'s' * (commits != 1)} and {releases} release{'s' * (releases != 1)}"
            f" in the last {WEEKS} weeks"
        )
        lines.append(
            f"| [**{repo}**](https://github.com/{OWNER}/{repo})"
            f' | <img src="assets/activity/{repo}.svg" width="{WIDTH}" height="24" alt="{alt}">'
            f" | [`{project.label}`]({project.link}) |"
        )
    lines += ["", f'<img src="assets/activity/legend.svg" width="{LEGEND_WIDTH}" height="14" alt="{LEGEND_ALT}">']
    return "\n".join(lines)


def graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    """Run one query and return its repository object."""
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "project-activity-script",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    name = f"{variables['owner']}/{variables['name']}"
    if "errors" in payload:
        raise RuntimeError(f"GitHub API error for {name}: {json.dumps(payload['errors'])}")
    repository: dict[str, Any] | None = payload["data"]["repository"]
    if repository is None:
        raise RuntimeError(f"GitHub repository not found: {name}")
    return repository


def fetch_nodes(
    query: str, variables: dict[str, Any], path: tuple[str, ...], token: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read all pages of the connection at path. Return the last repository object and all nodes."""
    nodes: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        repository = graphql(query, {**variables, "cursor": cursor}, token)
        connection = repository
        for key in path:
            connection = connection[key]
        nodes += connection["nodes"]
        if not connection["pageInfo"]["hasNextPage"]:
            return repository, nodes
        cursor = connection["pageInfo"]["endCursor"]


def fetch_project(repo: str, start: datetime, token: str) -> Project:
    names = {"owner": OWNER, "name": repo}
    history = ("defaultBranchRef", "target", "history")
    repository, commits = fetch_nodes(COMMITS_QUERY, {**names, "since": start.isoformat()}, history, token)
    tags = version_tags(fetch_nodes(TAGS_QUERY, names, ("refs",), token)[1])
    tag = newest_version_tag(tags)
    has_release = False
    if tag is not None:
        release = graphql(RELEASE_QUERY, {**names, "tag": tag}, token)["release"]
        has_release = release is not None and not release["isDraft"]
    label, link = latest_version(repo, tag, has_release, repository["defaultBranchRef"]["name"])
    return Project(
        repo,
        week_counts((parse_date(node["committedDate"]) for node in commits), start),
        week_counts((date for date, _ in tags), start),
        label,
        link,
    )


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("project-activity: the GITHUB_TOKEN environment variable is not set", file=sys.stderr)
        return 1
    start = window_start(datetime.now(UTC))
    readme_path = ROOT / "README.md"
    try:
        projects = [fetch_project(repo, start, token) for repo in PROJECTS]
        files = {f"{p.repo}.svg": strip_svg(p.repo, p.commits, p.releases) for p in projects}
        files["months.svg"] = months_svg(start)
        files["legend.svg"] = legend_svg()
        readme = replace_block(readme_path.read_text(encoding="utf-8"), readme_block(projects))
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (OUT_DIR / name).write_text(content, encoding="utf-8")
        readme_path.write_text(readme, encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"project-activity: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
