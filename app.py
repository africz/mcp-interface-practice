from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - lightweight fallback for local checks
    class FastMCP:  # type: ignore[override]
        def __init__(self, name: str):
            self.name = name
            self.tools: dict[str, object] = {}
            self.resources: dict[str, object] = {}
            self.prompts: dict[str, object] = {}

        def tool(self):
            def decorator(func):
                self.tools[func.__name__] = func
                return func

            return decorator

        def resource(self, uri: str):
            def decorator(func):
                self.resources[uri] = func
                return func

            return decorator

        def prompt(self):
            def decorator(func):
                self.prompts[func.__name__] = func
                return func

            return decorator

        def run(self, transport: str | None = None) -> None:
            return None

from git_utils import get_codeowners, get_repository_summary
from security import validate_repo_path

server = FastMCP("git-activity-analyzer")

# TODO: add bearer-token authentication before exposing an SSE transport publicly.
SSE_AUTH_TODO = "TODO: enforce SSE auth before enabling network-exposed transports."


@server.resource("git-activity://summary/{repo_path}")
def repository_summary(repo_path: str) -> dict:
    safe_repo_path = validate_repo_path(repo_path)
    return get_repository_summary(safe_repo_path)


@server.resource("git-activity://teams/backend")
def backend_team() -> dict:
    return {
        "team": "backend",
        "members": ["alice", "bob", "carol"],
        "focus": ["src/main.py", "src/api.py", "src/utils.py"],
    }


@server.resource("git-activity://ownership/CODEOWNERS")
def codeowners_resource() -> dict:
    return {
        "path": "CODEOWNERS",
        "entries": get_codeowners(),
    }

@server.prompt()
def review_git_activity(repo_path: str) -> str:
    safe_repo_path = validate_repo_path(repo_path)
    return "\n".join(
        [
            f"Review repository activity for {safe_repo_path}.",
            "Start with git-activity://summary/{repo_path}.",
            "Check git-activity://ownership/CODEOWNERS for declared ownership.",
            "Use analyze_hotspots(repo_path, days=30, branch=None) to find risky files.",
            "Use analyze_commit_patterns(repo_path, days=30, author=None) to summarize commit behavior.",
            "Compare the output with git-activity://teams/backend when ownership is unclear.",
        ]
    )


import tools  # noqa: E402,F401  # Registers @server.tool() decorators.
