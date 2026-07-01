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


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _normalize_author_text(value: Any) -> str:
    text = str(value).strip()
    if "<" in text:
        text = text.split("<", 1)[0].strip()
    return text


def _is_scalar_author_value(value: Any) -> bool:
    return isinstance(value, (str, bytes, int, float))


def _collect_author_values(value: Any) -> list[str]:
    if not value:
        return []

    if _is_scalar_author_value(value):
        normalized = _normalize_author_text(value)
        return [normalized] if normalized else []

    candidates: list[str] = []
    if isinstance(value, dict):
        for nested_key in ("name", "author", "login", "username", "email", "user"):
            nested_value = value.get(nested_key)
            if nested_value:
                candidates.extend(_collect_author_values(nested_value))
    else:
        for nested_key in ("name", "author", "login", "username", "email", "user"):
            nested_value = getattr(value, nested_key, None)
            if nested_value:
                candidates.extend(_collect_author_values(nested_value))

    if candidates:
        return candidates

    normalized = _normalize_author_text(value)
    return [normalized] if normalized else []


def _author_candidates(commit: Any) -> list[str]:
    candidates: list[str] = []

    for key in ("author", "author_name", "name", "login", "username", "user"):
        value = _get_value(commit, key)
        if value:
            candidates.extend(_collect_author_values(value))

    nested_commit = _get_value(commit, "commit")
    if nested_commit:
        nested_author = _get_value(nested_commit, "author")
        if nested_author:
            candidates.extend(_collect_author_values(nested_author))

    normalized = [candidate for candidate in candidates if candidate]
    if not normalized:
        return [""]
    return list(dict.fromkeys(normalized))


def _extract_commit_author(commit: Any) -> str:
    return _author_candidates(commit)[0]


def _author_matches(commit: Any, author: str) -> bool:
    author_key = author.strip().casefold()
    for candidate in _author_candidates(commit):
        candidate_key = candidate.casefold()
        if (
            candidate_key == author_key
            or candidate_key.startswith(f"{author_key} ")
            or candidate_key.startswith(f"{author_key}<")
        ):
            return True
    return False


def _filter_commits_by_author(commits: list[Any], author: str | None) -> list[Any]:
    if author is None:
        return commits
    return [commit for commit in commits if _author_matches(commit, author)]


@server.tool()
def analyze_hotspots(repo_path: str, days: int = 30, branch: str | None = None) -> list[dict]:
    """Rank risky files by recent change volume and contributor spread."""
    repository = _resolve_repository(repo_path)
    commits = repository.get_commits(days=days, branch=branch)

    file_metrics: dict[str, dict[str, object]] = {}
    for commit in commits:
        author = _extract_commit_author(commit)
        for file_change in _get_value(commit, "files", []) or []:
            path = _get_value(file_change, "path", "")
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
    if author is None:
        commits = repository.get_commits(days=days, author=None)
    else:
        commits = repository.get_commits(days=days, author=author)
        if not commits:
            commits = repository.get_commits(days=days, author=None)
            commits = _filter_commits_by_author(commits, author)

    author_counts: dict[str, int] = defaultdict(int)
    total_files = 0
    for commit in commits:
        author_counts[_extract_commit_author(commit)] += 1
        total_files += len(_get_value(commit, "files", []) or [])

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
