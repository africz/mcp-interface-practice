import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import https from "node:https";

export class GitActivityError extends Error {}

export function resolveRepoRoot(repoPath) {
  const candidate = path.resolve(repoPath);
  if (!fs.existsSync(candidate)) {
    throw new GitActivityError(`Repository path does not exist: ${candidate}`);
  }

  const target = fs.statSync(candidate).isDirectory() ? candidate : path.dirname(candidate);
  try {
    return execGit(target, ["rev-parse", "--show-toplevel"]).trim();
  } catch (error) {
    throw new GitActivityError(`${candidate} is not inside a git repository: ${error.message}`);
  }
}

function execGit(repoRoot, args) {
  return execFileSync("git", ["-C", repoRoot, ...args], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
}

function safeGit(repoRoot, args) {
  try {
    return execGit(repoRoot, args).trim();
  } catch {
    return null;
  }
}

function stripQuotes(value) {
  const trimmed = value.trim();
  if (trimmed.length >= 2 && (trimmed[0] === "'" || trimmed[0] === '"') && trimmed[0] === trimmed.at(-1)) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function normalizeRepoPath(repoRoot, rawValue) {
  const value = String(rawValue ?? "").trim();
  if (!value) {
    return value;
  }

  if (path.isAbsolute(value)) {
    const resolved = path.resolve(value);
    const relative = path.relative(repoRoot, resolved);
    if (!relative.startsWith("..") && !path.isAbsolute(relative)) {
      return relative.split(path.sep).join("/");
    }
    return resolved.split(path.sep).join("/");
  }

  return value.replaceAll("\\", "/").replace(/^\.\//, "");
}

function globSegmentToRegex(pattern) {
  let regex = "";
  for (let index = 0; index < pattern.length; index += 1) {
    const char = pattern[index];
    if (char === "*") {
      const next = pattern[index + 1];
      if (next === "*") {
        regex += ".*";
        index += 1;
      } else {
        regex += "[^/]*";
      }
    } else if (char === "?") {
      regex += ".";
    } else if ("\\.[]{}()+-^$|".includes(char)) {
      regex += `\\${char}`;
    } else {
      regex += char;
    }
  }
  return regex;
}

function matchesCodeownersPattern(pattern, repoPath) {
  const normalizedPath = repoPath.replace(/^\/+/, "");
  let normalizedPattern = String(pattern ?? "").trim();
  const anchored = normalizedPattern.startsWith("/");
  const dirOnly = normalizedPattern.endsWith("/");
  normalizedPattern = normalizedPattern.replace(/^\/+/, "");
  if (!normalizedPattern) {
    return false;
  }

  if (dirOnly) {
    const directory = normalizedPattern.replace(/\/+$/, "");
    if (normalizedPath === directory || normalizedPath.startsWith(`${directory}/`)) {
      return true;
    }
    return !anchored && `/${normalizedPath}/`.includes(`/${directory}/`);
  }

  if (!normalizedPattern.includes("/")) {
    return new RegExp(`^${globSegmentToRegex(normalizedPattern)}$`).test(path.posix.basename(normalizedPath));
  }

  const exact = new RegExp(`^${globSegmentToRegex(normalizedPattern)}$`);
  if (exact.test(normalizedPath)) {
    return true;
  }

  if (!anchored) {
    const unanchored = new RegExp(`^(?:.*/)?${globSegmentToRegex(normalizedPattern)}$`);
    return unanchored.test(normalizedPath);
  }

  return false;
}

function parseShortlog(output, limit) {
  const entries = [];
  const lines = output.split(/\r?\n/);
  for (const line of lines) {
    const match = line.trim().match(/^(\d+)\s+(.+?)\s+<([^>]+)>$/);
    if (!match) {
      continue;
    }
    entries.push({
      commits: Number.parseInt(match[1], 10),
      author: match[2],
      email: match[3],
    });
    if (entries.length >= limit) {
      break;
    }
  }
  return entries;
}

function parseHistory(output) {
  const commits = [];
  for (const rawEntry of output.split("\x1e")) {
    const entry = rawEntry.trim();
    if (!entry) {
      continue;
    }
    const lines = entry.split(/\r?\n/);
    const fields = lines[0].split("\x1f");
    if (fields.length !== 6) {
      continue;
    }
    const [hash, shortHash, authorName, authorEmail, date, subject] = fields;
    const files = [];
    let additions = 0;
    let deletions = 0;
    for (const line of lines.slice(1)) {
      const parts = line.split("\t");
      if (parts.length < 3) {
        continue;
      }
      const fileAdditions = /^\d+$/.test(parts[0]) ? Number.parseInt(parts[0], 10) : 0;
      const fileDeletions = /^\d+$/.test(parts[1]) ? Number.parseInt(parts[1], 10) : 0;
      const changedPath = parts.slice(2).join("\t");
      files.push({
        path: changedPath,
        additions: fileAdditions,
        deletions: fileDeletions,
      });
      additions += fileAdditions;
      deletions += fileDeletions;
    }
    commits.push({
      hash,
      short_hash: shortHash,
      author_name: authorName,
      author_email: authorEmail,
      date,
      subject,
      stats: {
        files_changed: files.length,
        additions,
        deletions,
      },
      files,
    });
  }
  return commits;
}

function parseHotspotHistory(output) {
  const commits = [];
  for (const rawEntry of output.split("\x1e")) {
    const entry = rawEntry.trim();
    if (!entry) {
      continue;
    }
    const lines = entry.split(/\r?\n/);
    const fields = lines[0].split("\x1f");
    if (fields.length !== 4) {
      continue;
    }
    const [hash, date, authorName, authorEmail] = fields;
    const files = [];
    for (const line of lines.slice(1)) {
      const parts = line.split("\t");
      if (parts.length < 3) {
        continue;
      }
      files.push({
        path: parts.slice(2).join("\t"),
        additions: /^\d+$/.test(parts[0]) ? Number.parseInt(parts[0], 10) : 0,
        deletions: /^\d+$/.test(parts[1]) ? Number.parseInt(parts[1], 10) : 0,
      });
    }
    commits.push({
      hash,
      date,
      author_name: authorName,
      author_email: authorEmail,
      files,
    });
  }
  return commits;
}

function parseWorkflowMetadata(workflowPath) {
  const text = fs.readFileSync(workflowPath, "utf8");
  const lines = text.split(/\r?\n/);
  let name = path.basename(workflowPath, path.extname(workflowPath));
  const triggers = new Set();

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const stripped = line.trim();
    if (!stripped || stripped.startsWith("#")) {
      continue;
    }
    if (stripped.startsWith("name:") && name === path.basename(workflowPath, path.extname(workflowPath))) {
      name = stripQuotes(stripped.split(":", 2)[1] ?? "") || name;
    }
    if (stripped.startsWith("on:")) {
      const after = (stripped.split(":", 2)[1] ?? "").trim();
      if (after.startsWith("[") && after.endsWith("]")) {
        for (const item of after.slice(1, -1).split(",")) {
          const trigger = stripQuotes(item);
          if (trigger) {
            triggers.add(trigger);
          }
        }
      } else if (after) {
        triggers.add(stripQuotes(after));
      } else {
        const baseIndent = line.length - line.trimStart().length;
        for (const child of lines.slice(index + 1)) {
          const childStripped = child.trim();
          if (!childStripped || childStripped.startsWith("#")) {
            continue;
          }
          const indent = child.length - child.trimStart().length;
          if (indent <= baseIndent) {
            break;
          }
          const key = childStripped.split(":", 1)[0].replace(/^- /, "").trim();
          if (key) {
            triggers.add(key);
          }
        }
      }
      break;
    }
  }

  return {
    name,
    triggers: [...triggers].sort(),
  };
}

function httpsJson(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(
      url,
      {
        headers: {
          Accept: "application/vnd.github+json",
          "User-Agent": "git-activity-analyzer-mcp",
        },
      },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if (response.statusCode && response.statusCode >= 400) {
            reject(new Error(`HTTP ${response.statusCode}: ${body.slice(0, 200)}`));
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(error);
          }
        });
      }
    );
    request.on("error", reject);
    request.setTimeout(5000, () => {
      request.destroy(new Error("Request timed out"));
    });
  });
}

export class GitActivityAnalyzer {
  constructor(repoPath) {
    this.repoRoot = resolveRepoRoot(repoPath);
  }

  summary() {
    const branch = safeGit(this.repoRoot, ["branch", "--show-current"]) || "DETACHED";
    const defaultBranch = this.#defaultBranch(branch);
    return {
      repo_name: path.basename(this.repoRoot),
      repo_root: this.repoRoot,
      current_branch: branch,
      default_branch: defaultBranch,
      head: this.#headCommit(),
      commit_count: this.#commitCount(),
      working_tree: this.#workingTreeStatus(),
      remotes: this.#remotes(),
      top_contributors: this.#shortlog({ limit: 5 }),
    };
  }

  commitHistory({ ref = "HEAD", limit = 20, author = null, path: targetPath = null, since = null, until = null } = {}) {
    const boundedLimit = clamp(Number(limit), 1, 200);
    const args = [
      "log",
      ref,
      `--max-count=${boundedLimit}`,
      "--date=iso-strict",
      "--pretty=format:%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s",
      "--numstat",
    ];
    if (author) {
      args.push(`--author=${author}`);
    }
    if (since) {
      args.push(`--since=${since}`);
    }
    if (until) {
      args.push(`--until=${until}`);
    }
    if (targetPath) {
      args.push("--", targetPath);
    }

    return {
      repo_root: this.repoRoot,
      ref,
      filters: {
        author,
        path: targetPath,
        since,
        until,
        limit: boundedLimit,
      },
      returned_commits: 0,
      commits: (() => {
        const commits = parseHistory(execGit(this.repoRoot, args));
        return commits;
      })(),
    };
  }

  rankHotspots({ sinceDays = 90, limit = 10, pathPrefix = null } = {}) {
    const boundedDays = clamp(Number(sinceDays), 1, 3650);
    const boundedLimit = clamp(Number(limit), 1, 200);
    const sinceDate = new Date(Date.now() - boundedDays * 24 * 60 * 60 * 1000).toISOString();
    const args = [
      "log",
      "--date=iso-strict",
      `--since=${sinceDate}`,
      "--pretty=format:%x1e%H%x1f%ad%x1f%an%x1f%ae",
      "--numstat",
    ];
    if (pathPrefix) {
      args.push("--", pathPrefix);
    }

    const aggregates = new Map();
    for (const commit of parseHotspotHistory(execGit(this.repoRoot, args))) {
      const seenPaths = new Set();
      for (const file of commit.files) {
        const metric = aggregates.get(file.path) ?? {
          path: file.path,
          touches: 0,
          additions: 0,
          deletions: 0,
          lines_changed: 0,
          last_touched: commit.date,
          contributors: new Map(),
        };
        if (!seenPaths.has(file.path)) {
          metric.touches += 1;
          seenPaths.add(file.path);
        }
        metric.additions += file.additions;
        metric.deletions += file.deletions;
        metric.lines_changed += file.additions + file.deletions;
        metric.last_touched = metric.last_touched < commit.date ? commit.date : metric.last_touched;
        metric.contributors.set(commit.author_name, (metric.contributors.get(commit.author_name) ?? 0) + 1);
        aggregates.set(file.path, metric);
      }
    }

    const hotspots = [...aggregates.values()]
      .sort((left, right) => {
        if (right.touches !== left.touches) return right.touches - left.touches;
        if (right.lines_changed !== left.lines_changed) return right.lines_changed - left.lines_changed;
        if (right.last_touched !== left.last_touched) return right.last_touched.localeCompare(left.last_touched);
        return right.path.localeCompare(left.path);
      })
      .slice(0, boundedLimit)
      .map((entry) => {
        const topContributors = [...entry.contributors.entries()]
          .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
          .slice(0, 3)
          .map(([author, touches]) => ({ author, touches }));
        return {
          path: entry.path,
          touches: entry.touches,
          additions: entry.additions,
          deletions: entry.deletions,
          lines_changed: entry.lines_changed,
          last_touched: entry.last_touched,
          top_contributors: topContributors,
          unique_contributors: entry.contributors.size,
        };
      });

    return {
      repo_root: this.repoRoot,
      window_days: boundedDays,
      path_prefix: pathPrefix,
      returned_hotspots: hotspots.length,
      hotspots,
    };
  }

  identifyOwners(paths, { maxContributors = 3 } = {}) {
    const boundedContributors = clamp(Number(maxContributors), 1, 20);
    const { file, rules } = this.#loadCodeowners();
    const results = paths.map((rawPath) => {
      const normalized = normalizeRepoPath(this.repoRoot, rawPath);
      const matchedRule = this.#matchCodeowners(rules, normalized);
      const contributors = this.#shortlog({ limit: boundedContributors, targetPath: normalized });
      const declaredOwners = matchedRule ? matchedRule.owners : [];
      let ownershipSource = "unknown";
      if (declaredOwners.length && contributors.length) {
        ownershipSource = "codeowners+git_shortlog";
      } else if (declaredOwners.length) {
        ownershipSource = "codeowners";
      } else if (contributors.length) {
        ownershipSource = "git_shortlog";
      }

      return {
        path: normalized,
        ownership_source: ownershipSource,
        codeowners: {
          file,
          pattern: matchedRule ? matchedRule.pattern : null,
          owners: declaredOwners,
        },
        recent_contributors: contributors,
        primary_owner: declaredOwners[0] ?? contributors[0]?.author ?? null,
      };
    });

    return {
      repo_root: this.repoRoot,
      codeowners_file: file,
      results,
    };
  }

  ownershipOverview() {
    const { file, rules } = this.#loadCodeowners();
    const activeFiles = this.rankHotspots({ limit: 5 }).hotspots.map((entry) => entry.path);
    return {
      repo_root: this.repoRoot,
      codeowners_file: file,
      codeowners_rules: rules.length,
      top_contributors: this.#shortlog({ limit: 10 }),
      active_file_owners: this.identifyOwners(activeFiles, { maxContributors: 3 }).results,
    };
  }

  async inspectCi({ includeRemoteRuns = true } = {}) {
    const workflows = this.#discoverWorkflows();
    const remoteCi = includeRemoteRuns ? await this.#githubActionsRuns() : {
      provider: "github-actions",
      status: "skipped",
      repository: null,
      latest_runs: [],
    };

    return {
      repo_root: this.repoRoot,
      ci_configured: workflows.length > 0,
      workflows,
      remote_ci: remoteCi,
    };
  }

  #headCommit() {
    try {
      const output = execGit(this.repoRoot, [
        "log",
        "-1",
        "--date=iso-strict",
        "--pretty=format:%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s",
      ]).trim();
      if (!output) {
        return {};
      }
      const [hash, shortHash, author, email, committedAt, subject] = output.split("\x1f");
      return {
        hash,
        short_hash: shortHash,
        author,
        email,
        committed_at: committedAt,
        subject,
      };
    } catch {
      return {};
    }
  }

  #commitCount() {
    const output = safeGit(this.repoRoot, ["rev-list", "--count", "HEAD"]);
    return output ? Number.parseInt(output, 10) : 0;
  }

  #workingTreeStatus() {
    const output = safeGit(this.repoRoot, ["status", "--porcelain=v1"]) ?? "";
    const lines = output.split(/\r?\n/).filter((line) => line.trim());
    const counts = {
      modified: 0,
      added: 0,
      deleted: 0,
      renamed: 0,
      conflicted: 0,
      untracked: 0,
    };

    for (const line of lines) {
      const status = line.slice(0, 2);
      if (status === "??") {
        counts.untracked += 1;
        continue;
      }
      const condensed = status.replaceAll(" ", "");
      if (condensed.includes("M")) counts.modified += 1;
      if (condensed.includes("A")) counts.added += 1;
      if (condensed.includes("D")) counts.deleted += 1;
      if (condensed.includes("R")) counts.renamed += 1;
      if (condensed.includes("U")) counts.conflicted += 1;
    }

    return {
      clean: lines.length === 0,
      changed_files: lines.length,
      ...counts,
    };
  }

  #remotes() {
    const output = safeGit(this.repoRoot, ["remote", "-v"]) ?? "";
    const remotes = new Map();
    for (const line of output.split(/\r?\n/)) {
      const match = line.trim().match(/^(\S+)\s+(\S+)\s+\((fetch|push)\)$/);
      if (!match) {
        continue;
      }
      const [, name, url, kind] = match;
      const remote = remotes.get(name) ?? { name };
      remote[kind] = url;
      remotes.set(name, remote);
    }
    return [...remotes.values()];
  }

  #defaultBranch(currentBranch) {
    const originHead = safeGit(this.repoRoot, ["symbolic-ref", "refs/remotes/origin/HEAD"]);
    if (originHead) {
      return originHead.split("/").at(-1);
    }
    const branches = (safeGit(this.repoRoot, ["for-each-ref", "--format=%(refname:short)", "refs/heads"]) ?? "")
      .split(/\r?\n/)
      .filter(Boolean);
    for (const preferred of ["main", "master", currentBranch]) {
      if (branches.includes(preferred)) {
        return preferred;
      }
    }
    return currentBranch === "DETACHED" ? null : currentBranch;
  }

  #shortlog({ limit, targetPath = null }) {
    const args = ["shortlog", "-sne", "--all"];
    if (targetPath) {
      args.push("--", targetPath);
    }
    return parseShortlog(safeGit(this.repoRoot, args) ?? "", limit);
  }

  #loadCodeowners() {
    for (const relativePath of [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"]) {
      const candidate = path.join(this.repoRoot, relativePath);
      if (!fs.existsSync(candidate)) {
        continue;
      }
      const rules = fs
        .readFileSync(candidate, "utf8")
        .split(/\r?\n/)
        .map((line, index) => ({ line, lineNumber: index + 1 }))
        .filter(({ line }) => {
          const trimmed = line.trim();
          return trimmed && !trimmed.startsWith("#");
        })
        .map(({ line, lineNumber }) => {
          const parts = line.trim().split(/\s+/);
          return {
            pattern: parts[0],
            owners: parts.slice(1),
            line_number: lineNumber,
          };
        })
        .filter((rule) => rule.owners.length > 0);
      return {
        file: candidate,
        rules,
      };
    }
    return {
      file: null,
      rules: [],
    };
  }

  #matchCodeowners(rules, repoPath) {
    let matched = null;
    for (const rule of rules) {
      if (matchesCodeownersPattern(rule.pattern, repoPath)) {
        matched = rule;
      }
    }
    return matched;
  }

  #discoverWorkflows() {
    const workflowDir = path.join(this.repoRoot, ".github", "workflows");
    if (!fs.existsSync(workflowDir)) {
      return [];
    }
    return fs
      .readdirSync(workflowDir)
      .filter((name) => /\.ya?ml$/i.test(name))
      .sort()
      .map((name) => {
        const workflowPath = path.join(workflowDir, name);
        return {
          path: path.relative(this.repoRoot, workflowPath).split(path.sep).join("/"),
          ...parseWorkflowMetadata(workflowPath),
        };
      });
  }

  async #githubActionsRuns() {
    const slug = this.#originGithubSlug();
    if (!slug) {
      return {
        provider: "github-actions",
        status: "unavailable",
        repository: null,
        latest_runs: [],
        error: "origin remote is not a GitHub repository",
      };
    }

    try {
      const payload = await httpsJson(`https://api.github.com/repos/${slug}/actions/runs?per_page=5`);
      return {
        provider: "github-actions",
        status: "ok",
        repository: slug,
        latest_runs: (payload.workflow_runs ?? []).map((run) => ({
          name: run.name,
          status: run.status,
          conclusion: run.conclusion,
          event: run.event,
          branch: run.head_branch,
          head_sha: run.head_sha,
          updated_at: run.updated_at,
          url: run.html_url,
        })),
      };
    } catch (error) {
      return {
        provider: "github-actions",
        status: "unavailable",
        repository: slug,
        latest_runs: [],
        error: String(error.message ?? error),
      };
    }
  }

  #originGithubSlug() {
    const origin = this.#remotes().find((remote) => remote.name === "origin");
    if (!origin) {
      return null;
    }
    for (const key of ["fetch", "push"]) {
      const url = origin[key];
      if (!url) {
        continue;
      }
      const match = url.match(/github\.com[:/]([^/]+)\/([^/]+?)(?:\.git)?$/);
      if (match) {
        return `${match[1]}/${match[2]}`;
      }
    }
    return null;
  }
}
