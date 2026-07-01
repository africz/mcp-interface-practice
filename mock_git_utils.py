from __future__ import annotations


SAMPLE_COMMITS = [
    {
        "sha": "a1",
        "author": "alice",
        "branch": "main",
        "files": [
            {"path": "src/main.py", "changes": 12},
            {"path": "src/utils.py", "changes": 4},
        ],
    },
    {
        "sha": "a2",
        "author": "bob",
        "branch": "main",
        "files": [
            {"path": "src/main.py", "changes": 7},
            {"path": "src/api.py", "changes": 9},
        ],
    },
    {
        "sha": "a3",
        "author": "alice",
        "branch": "main",
        "files": [
            {"path": "src/api.py", "changes": 3},
            {"path": "tests/test_api.py", "changes": 5},
        ],
    },
    {
        "sha": "a4",
        "author": "carol",
        "branch": "release",
        "files": [
            {"path": "src/main.py", "changes": 2},
        ],
    },
]


class MockGitRepository:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def get_commits(
        self,
        *,
        days: int = 30,
        branch: str | None = None,
        author: str | None = None,
    ) -> list[dict]:
        commits = SAMPLE_COMMITS
        if branch is not None:
            commits = [commit for commit in commits if commit["branch"] == branch]
        if author is not None:
            commits = [commit for commit in commits if commit["author"] == author]
        return list(commits)

    def get_summary(self) -> dict:
        return {
            "repo_path": self.repo_path,
            "branch": "main",
            "commit_count": len(SAMPLE_COMMITS),
            "top_authors": ["alice", "bob", "carol"],
        }
