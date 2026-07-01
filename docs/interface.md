# Git Activity Analyzer MCP Interface

## Goal

Provide a minimal MCP server that exposes repository context and two focused analyses for git activity triage.

## Questions The Agent Should Be Able To Answer

1. Which files changed most often in the last 30 days?
2. Which files have the highest risk based on change volume and contributor spread?
3. How many commits landed recently, and how broad was each commit on average?
4. Which authors are driving the recent change patterns?
5. What does the repository summary say about the target checkout?
6. What ownership signals are available from CODEOWNERS and the backend team view?

## Data Sources

- `git-activity://summary/{repo_path}` for repository-level context
- `git-activity://teams/backend` for a stable backend team snapshot
- `git-activity://ownership/CODEOWNERS` for declared ownership data
- `analyze_hotspots(repo_path, days=30, branch=None)` for file-level hotspot analysis
- `analyze_commit_patterns(repo_path, days=30, author=None)` for aggregate commit behavior

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

## Question Mapping

| Question | Primary interface element | Supporting source |
| --- | --- | --- |
| Which files changed most often recently? | `analyze_hotspots(...)` | `git-activity://summary/{repo_path}` |
| Which files look riskiest? | `analyze_hotspots(...)` | `git-activity://ownership/CODEOWNERS` |
| How many commits landed and how broad were they? | `analyze_commit_patterns(...)` | `git-activity://summary/{repo_path}` |
| Which authors are most active? | `analyze_commit_patterns(...)` | `git-activity://teams/backend` |
| What declared ownership exists for key files? | `git-activity://ownership/CODEOWNERS` | `git-activity://teams/backend` |
| How should an agent investigate a repo? | `review_git_activity(repo_path)` | all resources and both tools |

## Security Rules

- Repository access must be restricted to paths listed in `config/allowed_repos.json`.
- File access must reject traversal attempts before joining user input onto a repo path.
- SSE auth is not implemented yet, so the codebase keeps a clear TODO before any network-exposed SSE deployment.
