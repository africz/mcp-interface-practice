from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .analyzer import GitActivityAnalyzer, GitActivityError, resolve_repo_root


class GitActivityMCPServer:
    PROTOCOL_VERSION = "2025-06-18"

    def __init__(self, default_repo_path: str | Path | None = None):
        self.default_repo_root: Path | None = None
        if default_repo_path is not None:
            self.default_repo_root = resolve_repo_root(default_repo_path)
        else:
            try:
                self.default_repo_root = resolve_repo_root(Path.cwd())
            except GitActivityError:
                self.default_repo_root = None

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        jsonrpc = message.get("jsonrpc")
        if jsonrpc != "2.0":
            return self._error_response(message.get("id"), -32600, "Invalid Request")

        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}

        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return self._response(
                message_id,
                {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "serverInfo": {
                        "name": "git-activity-analyzer",
                        "version": "0.1.0",
                    },
                    "instructions": (
                        "Use resources for baseline repository context and tools for "
                        "filtered history, hotspots, CI, and ownership queries."
                    ),
                    "capabilities": {
                        "resources": {},
                        "tools": {},
                    },
                },
            )
        if method == "ping":
            return self._response(message_id, {})
        if method == "resources/list":
            return self._response(message_id, {"resources": self._list_resources()})
        if method == "resources/templates/list":
            return self._response(message_id, {"resourceTemplates": self._list_resource_templates()})
        if method == "resources/read":
            if not isinstance(params, dict) or "uri" not in params:
                return self._error_response(message_id, -32602, "resources/read requires a uri")
            try:
                content = self._read_resource(str(params["uri"]))
            except GitActivityError as error:
                return self._error_response(message_id, -32602, str(error))
            return self._response(message_id, {"contents": [content]})
        if method == "tools/list":
            return self._response(message_id, {"tools": self._list_tools()})
        if method == "tools/call":
            if not isinstance(params, dict) or "name" not in params:
                return self._error_response(message_id, -32602, "tools/call requires a tool name")
            result = self._call_tool(str(params["name"]), params.get("arguments") or {})
            return self._response(message_id, result)

        return self._error_response(message_id, -32601, f"Method not found: {method}")

    def run_stdio(self) -> None:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                response = self._error_response(None, -32700, f"Parse error: {error}")
            else:
                response = self.handle_message(message)

            if response is not None:
                print(json.dumps(response), flush=True)

    def _list_resources(self) -> list[dict[str, Any]]:
        if self.default_repo_root is None:
            return []
        return [
            {
                "uri": "git-activity://summary",
                "name": "Repository Summary",
                "description": "Repository metadata, HEAD commit, remotes, and working tree state",
                "mimeType": "application/json",
            },
            {
                "uri": "git-activity://ownership",
                "name": "Ownership Overview",
                "description": "Declared and inferred ownership for active files",
                "mimeType": "application/json",
            },
            {
                "uri": "git-activity://ci",
                "name": "CI Overview",
                "description": "Workflow inventory and best-effort latest GitHub Actions runs",
                "mimeType": "application/json",
            },
            {
                "uri": "git-activity://hotspots?since_days=30&limit=10",
                "name": "Default Hotspots",
                "description": "Most active files over the last 30 days",
                "mimeType": "application/json",
            },
        ]

    def _list_resource_templates(self) -> list[dict[str, Any]]:
        if self.default_repo_root is None:
            return []
        return [
            {
                "uriTemplate": (
                    "git-activity://history/{ref}?limit={limit}&author={author}"
                    "&path={path}&since={since}&until={until}"
                ),
                "name": "Commit History",
                "description": "Commit history for a ref with optional filters",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "git-activity://hotspots?since_days={since_days}&limit={limit}&path_prefix={path_prefix}",
                "name": "Hotspots",
                "description": "Recent churn ranking for files",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "git-activity://owners/{path}?max_contributors={max_contributors}",
                "name": "Owners For Path",
                "description": "Declared and inferred owners for a specific path",
                "mimeType": "application/json",
            },
        ]

    def _read_resource(self, uri: str) -> dict[str, Any]:
        analyzer = self._analyzer_for_repo(None)
        parsed = urlparse(uri)
        query = parse_qs(parsed.query)
        host = parsed.netloc
        path = parsed.path.lstrip("/")

        if host == "summary":
            data = analyzer.summary()
        elif host == "ownership":
            data = analyzer.ownership_overview()
        elif host == "ci":
            data = analyzer.inspect_ci(include_remote_runs=True)
        elif host == "hotspots":
            data = analyzer.rank_hotspots(
                since_days=self._query_int(query, "since_days", 30),
                limit=self._query_int(query, "limit", 10),
                path_prefix=self._query_value(query, "path_prefix"),
            )
        elif host == "history":
            ref = unquote(path) or "HEAD"
            data = analyzer.commit_history(
                ref=ref,
                limit=self._query_int(query, "limit", 20),
                author=self._query_value(query, "author"),
                path=self._query_value(query, "path"),
                since=self._query_value(query, "since"),
                until=self._query_value(query, "until"),
            )
        elif host == "owners":
            owner_path = unquote(path)
            if not owner_path:
                raise GitActivityError("owners resource requires a path")
            data = analyzer.identify_owners(
                [owner_path],
                max_contributors=self._query_int(query, "max_contributors", 3),
            )
        else:
            raise GitActivityError(f"Unknown resource URI: {uri}")

        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(data, indent=2, sort_keys=True),
        }

    def _list_tools(self) -> list[dict[str, Any]]:
        repo_path_property = {
            "type": "string",
            "description": "Optional path to a git repository. Defaults to the server's configured repo.",
        }
        return [
            {
                "name": "summarize_repository",
                "description": "Return repository metadata, working tree state, and top contributors.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": repo_path_property,
                    },
                },
            },
            {
                "name": "get_commit_history",
                "description": "Return commit history with optional author, path, and time filters.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": repo_path_property,
                        "ref": {"type": "string", "default": "HEAD"},
                        "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                        "author": {"type": "string"},
                        "path": {"type": "string"},
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                    },
                },
            },
            {
                "name": "rank_hotspots",
                "description": "Rank files by recent churn.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": repo_path_property,
                        "since_days": {"type": "integer", "default": 90, "minimum": 1, "maximum": 3650},
                        "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 200},
                        "path_prefix": {"type": "string"},
                    },
                },
            },
            {
                "name": "identify_owners",
                "description": "Match paths against CODEOWNERS and git contributor history.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": repo_path_property,
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        "max_contributors": {"type": "integer", "default": 3, "minimum": 1, "maximum": 20},
                    },
                    "required": ["paths"],
                },
            },
            {
                "name": "inspect_ci",
                "description": "Inspect local workflows and best-effort recent GitHub Actions runs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": repo_path_property,
                        "include_remote_runs": {"type": "boolean", "default": True},
                    },
                },
            },
        ]

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return self._tool_error("Tool arguments must be an object")

        try:
            if name == "summarize_repository":
                data = self._analyzer_for_repo(arguments.get("repo_path")).summary()
            elif name == "get_commit_history":
                data = self._analyzer_for_repo(arguments.get("repo_path")).commit_history(
                    ref=str(arguments.get("ref", "HEAD")),
                    limit=self._coerce_int(arguments.get("limit"), 20),
                    author=self._coerce_optional_str(arguments.get("author")),
                    path=self._coerce_optional_str(arguments.get("path")),
                    since=self._coerce_optional_str(arguments.get("since")),
                    until=self._coerce_optional_str(arguments.get("until")),
                )
            elif name == "rank_hotspots":
                data = self._analyzer_for_repo(arguments.get("repo_path")).rank_hotspots(
                    since_days=self._coerce_int(arguments.get("since_days"), 90),
                    limit=self._coerce_int(arguments.get("limit"), 10),
                    path_prefix=self._coerce_optional_str(arguments.get("path_prefix")),
                )
            elif name == "identify_owners":
                paths = arguments.get("paths")
                if not isinstance(paths, list) or not paths or not all(isinstance(item, str) for item in paths):
                    return self._tool_error("identify_owners requires a non-empty string array in paths")
                data = self._analyzer_for_repo(arguments.get("repo_path")).identify_owners(
                    paths,
                    max_contributors=self._coerce_int(arguments.get("max_contributors"), 3),
                )
            elif name == "inspect_ci":
                include_remote_runs = arguments.get("include_remote_runs", True)
                data = self._analyzer_for_repo(arguments.get("repo_path")).inspect_ci(
                    include_remote_runs=bool(include_remote_runs)
                )
            else:
                return self._tool_error(f"Unknown tool: {name}")
        except (GitActivityError, ValueError) as error:
            return self._tool_error(str(error))

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(data, indent=2, sort_keys=True),
                }
            ],
            "structuredContent": data,
            "isError": False,
        }

    def _analyzer_for_repo(self, repo_path: str | None) -> GitActivityAnalyzer:
        if repo_path:
            return GitActivityAnalyzer(repo_path)
        if self.default_repo_root is None:
            raise GitActivityError("No default repository configured. Pass repo_path or start the server with --repo.")
        return GitActivityAnalyzer(self.default_repo_root)

    def _response(self, message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": result,
        }

    def _error_response(self, message_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def _tool_error(self, message: str) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": message,
                }
            ],
            "isError": True,
        }

    def _query_value(self, query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key)
        if not values:
            return None
        return values[0]

    def _query_int(self, query: dict[str, list[str]], key: str, default: int) -> int:
        value = self._query_value(query, key)
        if value is None or value == "":
            return default
        return int(value)

    def _coerce_optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _coerce_int(self, value: Any, default: int) -> int:
        if value is None or value == "":
            return default
        return int(value)
