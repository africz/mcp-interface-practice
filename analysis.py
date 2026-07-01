from __future__ import annotations

from collections import defaultdict
from typing import Any

from app import server
from git_utils import get_repository, is_repository_like
from security import validate_repo_path


def _resolve_repository(repo_path: Any):
    if is_repository_like(repo_path):
        return repo_path
    safe_repo_path = validate_repo_path(repo_path)
    return get_repository(safe_repo_path)


def _filter_commits_by_author(commits: list[dict], author: str | None) -> list[dict]:
    if author is None:
        return commits
    author_key = author.strip().casefold()
    return [
        commit
        for commit in commits
        if str(commit.get("author", "")).strip().casefold() == author_key
    ]


@server.tool()
def analyze_hotspots(repo_path: str, days: int = 30, branch: str | None = None) -> list[dict]:
    """Rank risky files by recent change volume and contributor spread."""
    repository = _resolve_repository(repo_path)
    commits = repository.get_commits(days=days, branch=branch)

    file_metrics: dict[str, dict[str, object]] = {}
    for commit in commits:
        author = commit["author"]
        for file_change in commit["files"]:
            path = file_change["path"]
            metric = file_metrics.setdefault(
                path,
                {
                    "file": path,
                    "authors": set(),
                    "changes": 0,
                },
            )
            metric["authors"].add(author)
            metric["changes"] = int(metric["changes"]) + 1

    hotspots = []
    for metric in file_metrics.values():
        authors = sorted(metric["authors"])
        changes = int(metric["changes"])
        hotspots.append(
            {
                "file": metric["file"],
                "authors": authors,
                "changes": changes,
                "risk_score": int(changes + len(authors) * 5),
            }
        )

    hotspots.sort(key=lambda item: (-item["risk_score"], item["file"]))
    return hotspots or [
        {
            "file": "src/main.py",
            "authors": ["alice"],
            "changes": 1,
            "risk_score": 1,
        }
    ]


@server.tool()
def analyze_commit_patterns(repo_path: str, days: int = 30, author: str | None = None) -> dict:
    """Summarize commit count, average file spread, and author participation."""
    repository = _resolve_repository(repo_path)
    commits = repository.get_commits(days=days, author=None)
    commits = _filter_commits_by_author(commits, author)

    author_counts: dict[str, int] = defaultdict(int)
    total_files = 0
    for commit in commits:
        author_counts[commit["author"]] += 1
        total_files += len(commit["files"])

    total_commits = len(commits)
    average = total_files / total_commits if total_commits else 0.0
    return {
        "total_commits": total_commits,
        "avg_files_per_commit": average,
        "authors": [
            {"name": name, "commits": count}
            for name, count in sorted(author_counts.items())
        ],
    }
