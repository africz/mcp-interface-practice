from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any


class GitActivityError(RuntimeError):
    """Raised when repository inspection cannot be completed."""


@dataclass(frozen=True)
class CodeownersRule:
    pattern: str
    owners: list[str]
    line_number: int


def resolve_repo_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise GitActivityError(f"Repository path does not exist: {candidate}")

    target = candidate if candidate.is_dir() else candidate.parent
    process = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        stderr = process.stderr.strip() or process.stdout.strip() or "Unknown git error"
        raise GitActivityError(f"{candidate} is not inside a git repository: {stderr}")
    return Path(process.stdout.strip()).resolve()


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


class GitActivityAnalyzer:
    def __init__(self, repo_path: str | Path):
        self.repo_root = resolve_repo_root(repo_path)

    def summary(self) -> dict[str, Any]:
        branch = self._safe_git("branch", "--show-current") or "DETACHED"
        head = self._head_commit()
        remotes = self._remotes()
        contributors = self._shortlog(limit=5)
        default_branch = self._default_branch(branch)
        return {
            "repo_name": self.repo_root.name,
            "repo_root": str(self.repo_root),
            "current_branch": branch,
            "default_branch": default_branch,
            "head": head,
            "commit_count": self._commit_count(),
            "working_tree": self._working_tree_status(),
            "remotes": remotes,
            "top_contributors": contributors,
        }

    def commit_history(
        self,
        *,
        ref: str = "HEAD",
        limit: int = 20,
        author: str | None = None,
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        args = [
            "log",
            ref,
            f"--max-count={limit}",
            "--date=iso-strict",
            "--pretty=format:%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s",
            "--numstat",
        ]
        if author:
            args.append(f"--author={author}")
        if since:
            args.append(f"--since={since}")
        if until:
            args.append(f"--until={until}")
        if path:
            args.extend(["--", path])

        output = self._git(*args)
        commits = self._parse_history(output)
        return {
            "repo_root": str(self.repo_root),
            "ref": ref,
            "filters": {
                "author": author,
                "path": path,
                "since": since,
                "until": until,
                "limit": limit,
            },
            "returned_commits": len(commits),
            "commits": commits,
        }

    def rank_hotspots(
        self,
        *,
        since_days: int = 90,
        limit: int = 10,
        path_prefix: str | None = None,
    ) -> dict[str, Any]:
        since_days = max(1, min(since_days, 3650))
        limit = max(1, min(limit, 200))
        since_date = datetime.now(timezone.utc) - timedelta(days=since_days)
        args = [
            "log",
            "--date=iso-strict",
            f"--since={since_date.isoformat()}",
            "--pretty=format:%x1e%H%x1f%ad%x1f%an%x1f%ae",
            "--numstat",
        ]
        if path_prefix:
            args.extend(["--", path_prefix])

        output = self._git(*args)
        aggregates: dict[str, dict[str, Any]] = {}
        for commit in self._parse_hotspot_history(output):
            commit_paths: set[str] = set()
            for change in commit["files"]:
                changed_path = change["path"]
                metric = aggregates.setdefault(
                    changed_path,
                    {
                        "path": changed_path,
                        "touches": 0,
                        "additions": 0,
                        "deletions": 0,
                        "lines_changed": 0,
                        "last_touched": commit["date"],
                        "contributors": Counter(),
                    },
                )
                if changed_path not in commit_paths:
                    metric["touches"] += 1
                    commit_paths.add(changed_path)
                metric["additions"] += change["additions"]
                metric["deletions"] += change["deletions"]
                metric["lines_changed"] += change["additions"] + change["deletions"]
                metric["contributors"][commit["author_name"]] += 1
                if commit["date"] > metric["last_touched"]:
                    metric["last_touched"] = commit["date"]

        ranked = sorted(
            aggregates.values(),
            key=lambda item: (
                item["touches"],
                item["lines_changed"],
                item["last_touched"],
                item["path"],
            ),
            reverse=True,
        )

        hotspots = []
        for entry in ranked[:limit]:
            contributor_counts = entry.pop("contributors")
            hotspots.append(
                {
                    **entry,
                    "top_contributors": [
                        {"author": name, "touches": touches}
                        for name, touches in contributor_counts.most_common(3)
                    ],
                    "unique_contributors": len(contributor_counts),
                }
            )

        return {
            "repo_root": str(self.repo_root),
            "window_days": since_days,
            "path_prefix": path_prefix,
            "returned_hotspots": len(hotspots),
            "hotspots": hotspots,
        }

    def identify_owners(
        self,
        paths: list[str],
        *,
        max_contributors: int = 3,
    ) -> dict[str, Any]:
        max_contributors = max(1, min(max_contributors, 20))
        rules_file, rules = self._load_codeowners()
        results = []
        for raw_path in paths:
            normalized = self._normalize_repo_path(raw_path)
            matched_rule = self._match_codeowners(rules, normalized)
            contributors = self._shortlog(limit=max_contributors, path=normalized)
            declared_owners = matched_rule.owners if matched_rule else []
            ownership_source = "unknown"
            if declared_owners and contributors:
                ownership_source = "codeowners+git_shortlog"
            elif declared_owners:
                ownership_source = "codeowners"
            elif contributors:
                ownership_source = "git_shortlog"

            results.append(
                {
                    "path": normalized,
                    "ownership_source": ownership_source,
                    "codeowners": {
                        "file": str(rules_file) if rules_file else None,
                        "pattern": matched_rule.pattern if matched_rule else None,
                        "owners": declared_owners,
                    },
                    "recent_contributors": contributors,
                    "primary_owner": declared_owners[0] if declared_owners else (
                        contributors[0]["author"] if contributors else None
                    ),
                }
            )

        return {
            "repo_root": str(self.repo_root),
            "codeowners_file": str(rules_file) if rules_file else None,
            "results": results,
        }

    def ownership_overview(self) -> dict[str, Any]:
        rules_file, rules = self._load_codeowners()
        active_files = [entry["path"] for entry in self.rank_hotspots(limit=5)["hotspots"]]
        active_owners = self.identify_owners(active_files, max_contributors=3)["results"]
        return {
            "repo_root": str(self.repo_root),
            "codeowners_file": str(rules_file) if rules_file else None,
            "codeowners_rules": len(rules),
            "top_contributors": self._shortlog(limit=10),
            "active_file_owners": active_owners,
        }

    def inspect_ci(self, *, include_remote_runs: bool = True) -> dict[str, Any]:
        workflows = self._discover_workflows()
        remote_ci = self._github_actions_runs() if include_remote_runs else {
            "provider": "github-actions",
            "status": "skipped",
            "repository": None,
            "latest_runs": [],
        }
        return {
            "repo_root": str(self.repo_root),
            "ci_configured": bool(workflows),
            "workflows": workflows,
            "remote_ci": remote_ci,
        }

    def _git(self, *args: str) -> str:
        process = subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            stderr = process.stderr.strip() or process.stdout.strip() or "Unknown git error"
            raise GitActivityError(stderr)
        return process.stdout

    def _safe_git(self, *args: str) -> str | None:
        try:
            return self._git(*args).strip()
        except GitActivityError:
            return None

    def _head_commit(self) -> dict[str, Any]:
        try:
            output = self._git(
                "log",
                "-1",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s",
            ).strip()
        except GitActivityError:
            return {}

        if not output:
            return {}
        full_hash, short_hash, author, email, committed_at, subject = output.split("\x1f", 5)
        return {
            "hash": full_hash,
            "short_hash": short_hash,
            "author": author,
            "email": email,
            "committed_at": committed_at,
            "subject": subject,
        }

    def _commit_count(self) -> int:
        output = self._safe_git("rev-list", "--count", "HEAD")
        return int(output) if output else 0

    def _working_tree_status(self) -> dict[str, Any]:
        output = self._safe_git("status", "--porcelain=v1") or ""
        lines = [line for line in output.splitlines() if line.strip()]
        counts: Counter[str] = Counter()
        for line in lines:
            status = line[:2]
            if status == "??":
                counts["untracked"] += 1
                continue
            condensed = status.replace(" ", "")
            if "M" in condensed:
                counts["modified"] += 1
            if "A" in condensed:
                counts["added"] += 1
            if "D" in condensed:
                counts["deleted"] += 1
            if "R" in condensed:
                counts["renamed"] += 1
            if "U" in condensed:
                counts["conflicted"] += 1

        return {
            "clean": not lines,
            "changed_files": len(lines),
            "modified": counts["modified"],
            "added": counts["added"],
            "deleted": counts["deleted"],
            "renamed": counts["renamed"],
            "conflicted": counts["conflicted"],
            "untracked": counts["untracked"],
        }

    def _remotes(self) -> list[dict[str, Any]]:
        output = self._safe_git("remote", "-v") or ""
        grouped: dict[str, dict[str, Any]] = {}
        pattern = re.compile(r"^(?P<name>\S+)\s+(?P<url>\S+)\s+\((?P<kind>fetch|push)\)$")
        for line in output.splitlines():
            match = pattern.match(line.strip())
            if not match:
                continue
            remote = grouped.setdefault(match.group("name"), {"name": match.group("name")})
            remote[match.group("kind")] = match.group("url")
        return list(grouped.values())

    def _default_branch(self, current_branch: str) -> str | None:
        origin_head = self._safe_git("symbolic-ref", "refs/remotes/origin/HEAD")
        if origin_head:
            return origin_head.rsplit("/", maxsplit=1)[-1]
        branches = (self._safe_git("for-each-ref", "--format=%(refname:short)", "refs/heads") or "").splitlines()
        for preferred in ("main", "master", current_branch):
            if preferred in branches:
                return preferred
        return current_branch if current_branch != "DETACHED" else None

    def _shortlog(self, *, limit: int, path: str | None = None) -> list[dict[str, Any]]:
        args = ["shortlog", "-sne", "--all"]
        if path:
            args.extend(["--", path])
        output = self._safe_git(*args) or ""
        results = []
        pattern = re.compile(r"^\s*(\d+)\s+(.+?)\s+<([^>]+)>$")
        for line in output.splitlines():
            match = pattern.match(line.strip())
            if not match:
                continue
            results.append(
                {
                    "commits": int(match.group(1)),
                    "author": match.group(2),
                    "email": match.group(3),
                }
            )
            if len(results) >= limit:
                break
        return results

    def _parse_history(self, output: str) -> list[dict[str, Any]]:
        commits = []
        for raw_entry in output.split("\x1e"):
            entry = raw_entry.strip()
            if not entry:
                continue
            lines = entry.splitlines()
            header = lines[0]
            fields = header.split("\x1f")
            if len(fields) != 6:
                continue
            full_hash, short_hash, author_name, author_email, committed_at, subject = fields
            files = []
            additions = 0
            deletions = 0
            for line in lines[1:]:
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                add_raw, del_raw, path = parts
                add_count = int(add_raw) if add_raw.isdigit() else 0
                del_count = int(del_raw) if del_raw.isdigit() else 0
                files.append(
                    {
                        "path": path,
                        "additions": add_count,
                        "deletions": del_count,
                    }
                )
                additions += add_count
                deletions += del_count

            commits.append(
                {
                    "hash": full_hash,
                    "short_hash": short_hash,
                    "author_name": author_name,
                    "author_email": author_email,
                    "date": committed_at,
                    "subject": subject,
                    "stats": {
                        "files_changed": len(files),
                        "additions": additions,
                        "deletions": deletions,
                    },
                    "files": files,
                }
            )
        return commits

    def _parse_hotspot_history(self, output: str) -> list[dict[str, Any]]:
        commits = []
        for raw_entry in output.split("\x1e"):
            entry = raw_entry.strip()
            if not entry:
                continue
            lines = entry.splitlines()
            header = lines[0]
            fields = header.split("\x1f")
            if len(fields) != 4:
                continue
            full_hash, committed_at, author_name, author_email = fields
            files = []
            for line in lines[1:]:
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                add_raw, del_raw, path = parts
                files.append(
                    {
                        "path": path,
                        "additions": int(add_raw) if add_raw.isdigit() else 0,
                        "deletions": int(del_raw) if del_raw.isdigit() else 0,
                    }
                )
            commits.append(
                {
                    "hash": full_hash,
                    "date": committed_at,
                    "author_name": author_name,
                    "author_email": author_email,
                    "files": files,
                }
            )
        return commits

    def _load_codeowners(self) -> tuple[Path | None, list[CodeownersRule]]:
        for relative in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
            candidate = self.repo_root / relative
            if candidate.exists():
                rules = []
                for index, line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), start=1):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    parts = stripped.split()
                    if len(parts) < 2:
                        continue
                    rules.append(CodeownersRule(pattern=parts[0], owners=parts[1:], line_number=index))
                return candidate, rules
        return None, []

    def _match_codeowners(
        self,
        rules: list[CodeownersRule],
        repo_path: str,
    ) -> CodeownersRule | None:
        matched = None
        for rule in rules:
            if self._matches_codeowners_pattern(rule.pattern, repo_path):
                matched = rule
        return matched

    def _matches_codeowners_pattern(self, pattern: str, repo_path: str) -> bool:
        normalized_path = repo_path.lstrip("/")
        normalized_pattern = pattern.strip()
        anchored = normalized_pattern.startswith("/")
        dir_only = normalized_pattern.endswith("/")
        normalized_pattern = normalized_pattern.lstrip("/")
        if not normalized_pattern:
            return False

        if dir_only:
            directory = normalized_pattern.rstrip("/")
            if normalized_path == directory or normalized_path.startswith(directory + "/"):
                return True
            if not anchored:
                return f"/{directory}/" in f"/{normalized_path}/"
            return False

        if "/" not in normalized_pattern:
            basename = PurePosixPath(normalized_path).name
            return fnmatch(basename, normalized_pattern)

        if fnmatch(normalized_path, normalized_pattern):
            return True

        return not anchored and fnmatch(normalized_path, f"**/{normalized_pattern}")

    def _normalize_repo_path(self, value: str) -> str:
        raw = value.strip()
        if not raw:
            return raw
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(self.repo_root)
            except ValueError:
                return raw.replace("\\", "/")
        return str(PurePosixPath(str(candidate).replace("\\", "/"))).lstrip("./")

    def _discover_workflows(self) -> list[dict[str, Any]]:
        workflow_dir = self.repo_root / ".github" / "workflows"
        if not workflow_dir.exists():
            return []

        workflows = []
        for path in sorted(workflow_dir.glob("*.y*ml")):
            metadata = self._parse_workflow_metadata(path)
            workflows.append(
                {
                    "path": str(path.relative_to(self.repo_root)),
                    **metadata,
                }
            )
        return workflows

    def _parse_workflow_metadata(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        name = path.stem
        triggers: list[str] = []

        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("name:") and name == path.stem:
                name = _strip_quotes(stripped.split(":", 1)[1].strip()) or path.stem
            if stripped.startswith("on:"):
                after = stripped.split(":", 1)[1].strip()
                if after.startswith("[") and after.endswith("]"):
                    triggers.extend(
                        _strip_quotes(item.strip())
                        for item in after[1:-1].split(",")
                        if item.strip()
                    )
                elif after:
                    triggers.append(_strip_quotes(after))
                else:
                    base_indent = len(line) - len(line.lstrip())
                    for child in lines[index + 1 :]:
                        if not child.strip() or child.strip().startswith("#"):
                            continue
                        indent = len(child) - len(child.lstrip())
                        if indent <= base_indent:
                            break
                        child_key = child.strip().split(":", 1)[0].lstrip("- ").strip()
                        if child_key:
                            triggers.append(child_key)
                break

        return {
            "name": name,
            "triggers": sorted(set(triggers)),
        }

    def _github_actions_runs(self) -> dict[str, Any]:
        slug = self._origin_github_slug()
        if not slug:
            return {
                "provider": "github-actions",
                "status": "unavailable",
                "repository": None,
                "latest_runs": [],
                "error": "origin remote is not a GitHub repository",
            }

        url = f"https://api.github.com/repos/{slug}/actions/runs?per_page=5"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "git-activity-analyzer-mcp",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            return {
                "provider": "github-actions",
                "status": "unavailable",
                "repository": slug,
                "latest_runs": [],
                "error": str(error),
            }

        latest_runs = []
        for run in payload.get("workflow_runs", []):
            latest_runs.append(
                {
                    "name": run.get("name"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "event": run.get("event"),
                    "branch": run.get("head_branch"),
                    "head_sha": run.get("head_sha"),
                    "updated_at": run.get("updated_at"),
                    "url": run.get("html_url"),
                }
            )

        return {
            "provider": "github-actions",
            "status": "ok",
            "repository": slug,
            "latest_runs": latest_runs,
        }

    def _origin_github_slug(self) -> str | None:
        remotes = self._remotes()
        origin = next((remote for remote in remotes if remote["name"] == "origin"), None)
        if not origin:
            return None

        for key in ("fetch", "push"):
            url = origin.get(key)
            if not url:
                continue
            match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", url)
            if match:
                return f"{match.group('owner')}/{match.group('repo')}"
        return None
