from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "allowed_repos.json"


def _load_allowed_roots() -> list[Path]:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return [(PROJECT_ROOT / entry).resolve() for entry in payload.get("allowed_repos", [])]


def validate_repo_path(repo_path: str) -> str:
    candidate = Path(repo_path)
    resolved = (PROJECT_ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    allowed_roots = _load_allowed_roots()

    for allowed_root in allowed_roots:
        try:
            resolved.relative_to(allowed_root)
            return str(resolved)
        except ValueError:
            continue

    raise ValueError(f"{repo_path} is not inside any allowed repository root")


def validate_file_path(repo_root: str, relative_path: str) -> str:
    base = Path(repo_root)
    candidate = base / relative_path
    normalized = candidate.resolve(strict=False) if base.is_absolute() else candidate

    if ".." in Path(relative_path).parts:
        raise ValueError("path traversal detected")

    if base.is_absolute():
        try:
            normalized.relative_to(base.resolve(strict=False))
        except ValueError as error:
            raise ValueError("path traversal detected") from error
        return str(normalized)

    return str(candidate)
