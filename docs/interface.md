# Git Activity Analyzer MCP Interface

## Goal

Provide a minimal MCP server that exposes repository context and two focused analyses for git activity triage.

## Resources

The server registers these stable `git-activity://` resources:

- `git-activity://summary/{repo_path}`
  Returns a compact repository summary for the requested repo path.
- `git-activity://teams/backend`
  Returns the backend team roster and the files they most often touch.
- `git-activity://ownership/CODEOWNERS`
  Returns declared ownership entries derived from a simplified CODEOWNERS view.

## Tools

### `analyze_hotspots(repo_path, days=30, branch=None)`

Returns a non-empty list of hotspot records. Each record contains:

- `file`
- `authors`
- `changes`
- `risk_score`

`risk_score` is always an integer.

### `analyze_commit_patterns(repo_path, days=30, author=None)`

Returns a dictionary with:

- `total_commits`
- `avg_files_per_commit`
- `authors`

The mock fixture is intentionally configured so the default call returns `total_commits == 4`.

## Prompt Template

### `review_git_activity(repo_path)`

Guides an agent to:

1. read `git-activity://summary/{repo_path}`
2. inspect `git-activity://ownership/CODEOWNERS`
3. call `analyze_hotspots(...)`
4. call `analyze_commit_patterns(...)`
5. compare the result with `git-activity://teams/backend`

## Security Rules

- Repository access must be restricted to paths listed in `config/allowed_repos.json`.
- File access must reject traversal attempts before joining user input onto a repo path.
- SSE auth is not implemented yet, so the codebase keeps a clear TODO before any network-exposed SSE deployment.
