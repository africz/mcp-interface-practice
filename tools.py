from __future__ import annotations

from typing import Any

import analysis
from app import server


@server.tool()
def analyze_hotspots(repo_path: str, days: int = 30, branch: str | None = None) -> list[dict[str, Any]]:
    """Rank files by recent change volume and contributor spread to flag risky hotspots."""
    return analysis.analyze_hotspots(repo_path, days=days, branch=branch)


@server.tool()
def analyze_commit_patterns(repo_path: str, days: int = 30, author: str | None = None) -> dict[str, Any]:
    """Summarize commit volume, average file spread per commit, and author participation."""
    return analysis.analyze_commit_patterns(repo_path, days=days, author=author)
