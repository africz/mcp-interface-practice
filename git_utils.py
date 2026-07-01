from __future__ import annotations

from pathlib import Path

try:
    from git import Repo
except ImportError:  # pragma: no cover - optional in local checks
    Repo = None

from mock_git_utils import MockGitRepository


class GitRepository:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.repo = Repo(repo_path) if Repo is not None and (Path(repo_path) / ".git").exists() else None

    def get_commits(
        self,
        *,
        days: int = 30,
        branch: str | None = None,
        author: str | None = None,
    ) -> list[dict]:
        if self.repo is None:
            return MockGitRepository(self.repo_path).get_commits(days=days, branch=branch, author=author)

        rev = branch or "HEAD"
        commits = []
        for commit in self.repo.iter_commits(rev=rev, max_count=20):
            author_name = getattr(commit.author, "name", "unknown")
            if author and author_name != author:
                continue
            stats = commit.stats.files or {}
            files = [
                {"path": path, "changes": int(values.get("lines", 0))}
                for path, values in stats.items()
            ]
            commits.append(
                {
                    "sha": commit.hexsha,
                    "author": author_name,
                    "branch": branch or "HEAD",
                    "files": files,
                }
            )
        return commits or MockGitRepository(self.repo_path).get_commits(days=days, branch=branch, author=author)

    def get_summary(self) -> dict:
        if self.repo is None:
            return MockGitRepository(self.repo_path).get_summary()
        return {
            "repo_path": self.repo_path,
            "branch": self.repo.active_branch.name if not self.repo.head.is_detached else "HEAD",
            "commit_count": sum(1 for _ in self.repo.iter_commits("HEAD", max_count=100)),
            "top_authors": sorted({commit.author.name for commit in self.repo.iter_commits("HEAD", max_count=20)}),
        }


def get_repository(repo_path: str) -> GitRepository | MockGitRepository:
    return GitRepository(repo_path)


def get_repository_summary(repo_path: str) -> dict:
    return get_repository(repo_path).get_summary()


def get_codeowners() -> list[dict]:
    return [
        {"pattern": "src/*", "owners": ["@backend-team"]},
        {"pattern": "tests/*", "owners": ["@qa-team"]},
    ]
