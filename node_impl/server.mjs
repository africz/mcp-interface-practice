import { GitActivityAnalyzer, GitActivityError, resolveRepoRoot } from "./analyzer.mjs";

function toolError(message) {
  return {
    content: [{ type: "text", text: message }],
    isError: true,
  };
}

function response(id, result) {
  return { jsonrpc: "2.0", id, result };
}

function errorResponse(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

export class GitActivityMCPServer {
  static PROTOCOL_VERSION = "2025-06-18";

  constructor(defaultRepoPath = null) {
    this.defaultRepoRoot = null;
    if (defaultRepoPath) {
      this.defaultRepoRoot = resolveRepoRoot(defaultRepoPath);
      return;
    }
    try {
      this.defaultRepoRoot = resolveRepoRoot(process.cwd());
    } catch {
      this.defaultRepoRoot = null;
    }
  }

  async handleMessage(message) {
    if (message?.jsonrpc !== "2.0") {
      return errorResponse(message?.id ?? null, -32600, "Invalid Request");
    }

    const method = message.method;
    const params = message.params ?? {};
    const messageId = message.id ?? null;

    if (method === "notifications/initialized") {
      return null;
    }
    if (method === "initialize") {
      return response(messageId, {
        protocolVersion: GitActivityMCPServer.PROTOCOL_VERSION,
        serverInfo: {
          name: "git-activity-analyzer",
          version: "0.1.0",
        },
        instructions:
          "Use resources for baseline repository context and tools for filtered history, hotspots, CI, and ownership queries.",
        capabilities: {
          resources: {},
          tools: {},
        },
      });
    }
    if (method === "ping") {
      return response(messageId, {});
    }
    if (method === "resources/list") {
      return response(messageId, { resources: this.#listResources() });
    }
    if (method === "resources/templates/list") {
      return response(messageId, { resourceTemplates: this.#listResourceTemplates() });
    }
    if (method === "resources/read") {
      if (!params || typeof params.uri !== "string") {
        return errorResponse(messageId, -32602, "resources/read requires a uri");
      }
      try {
        const content = await this.#readResource(params.uri);
        return response(messageId, { contents: [content] });
      } catch (error) {
        return errorResponse(messageId, -32602, String(error.message ?? error));
      }
    }
    if (method === "tools/list") {
      return response(messageId, { tools: this.#listTools() });
    }
    if (method === "tools/call") {
      if (!params || typeof params.name !== "string") {
        return errorResponse(messageId, -32602, "tools/call requires a tool name");
      }
      const result = await this.#callTool(params.name, params.arguments ?? {});
      return response(messageId, result);
    }

    return errorResponse(messageId, -32601, `Method not found: ${method}`);
  }

  async runStdio() {
    process.stdin.setEncoding("utf8");
    let buffer = "";
    for await (const chunk of process.stdin) {
      buffer += chunk;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) {
          continue;
        }
        let result;
        try {
          const message = JSON.parse(trimmed);
          result = await this.handleMessage(message);
        } catch (error) {
          result = errorResponse(null, -32700, `Parse error: ${String(error.message ?? error)}`);
        }
        if (result !== null) {
          process.stdout.write(`${JSON.stringify(result)}\n`);
        }
      }
    }
  }

  #listResources() {
    if (!this.defaultRepoRoot) {
      return [];
    }
    return [
      {
        uri: "git-activity://summary",
        name: "Repository Summary",
        description: "Repository metadata, HEAD commit, remotes, and working tree state",
        mimeType: "application/json",
      },
      {
        uri: "git-activity://ownership",
        name: "Ownership Overview",
        description: "Declared and inferred ownership for active files",
        mimeType: "application/json",
      },
      {
        uri: "git-activity://ci",
        name: "CI Overview",
        description: "Workflow inventory and best-effort latest GitHub Actions runs",
        mimeType: "application/json",
      },
      {
        uri: "git-activity://hotspots?since_days=30&limit=10",
        name: "Default Hotspots",
        description: "Most active files over the last 30 days",
        mimeType: "application/json",
      },
    ];
  }

  #listResourceTemplates() {
    if (!this.defaultRepoRoot) {
      return [];
    }
    return [
      {
        uriTemplate: "git-activity://history/{ref}?limit={limit}&author={author}&path={path}&since={since}&until={until}",
        name: "Commit History",
        description: "Commit history for a ref with optional filters",
        mimeType: "application/json",
      },
      {
        uriTemplate: "git-activity://hotspots?since_days={since_days}&limit={limit}&path_prefix={path_prefix}",
        name: "Hotspots",
        description: "Recent churn ranking for files",
        mimeType: "application/json",
      },
      {
        uriTemplate: "git-activity://owners/{path}?max_contributors={max_contributors}",
        name: "Owners For Path",
        description: "Declared and inferred owners for a specific path",
        mimeType: "application/json",
      },
    ];
  }

  async #readResource(uri) {
    const analyzer = this.#analyzerForRepo(null);
    const parsed = new URL(uri);
    const host = parsed.hostname;
    const resourcePath = decodeURIComponent(parsed.pathname.replace(/^\/+/, ""));

    let data;
    if (host === "summary") {
      data = analyzer.summary();
    } else if (host === "ownership") {
      data = analyzer.ownershipOverview();
    } else if (host === "ci") {
      data = await analyzer.inspectCi({ includeRemoteRuns: true });
    } else if (host === "hotspots") {
      data = analyzer.rankHotspots({
        sinceDays: this.#queryInt(parsed, "since_days", 30),
        limit: this.#queryInt(parsed, "limit", 10),
        pathPrefix: this.#queryValue(parsed, "path_prefix"),
      });
    } else if (host === "history") {
      data = analyzer.commitHistory({
        ref: resourcePath || "HEAD",
        limit: this.#queryInt(parsed, "limit", 20),
        author: this.#queryValue(parsed, "author"),
        path: this.#queryValue(parsed, "path"),
        since: this.#queryValue(parsed, "since"),
        until: this.#queryValue(parsed, "until"),
      });
      data.returned_commits = data.commits.length;
    } else if (host === "owners") {
      if (!resourcePath) {
        throw new GitActivityError("owners resource requires a path");
      }
      data = analyzer.identifyOwners([resourcePath], {
        maxContributors: this.#queryInt(parsed, "max_contributors", 3),
      });
    } else {
      throw new GitActivityError(`Unknown resource URI: ${uri}`);
    }

    return {
      uri,
      mimeType: "application/json",
      text: JSON.stringify(data, null, 2),
    };
  }

  #listTools() {
    const repoPathProperty = {
      type: "string",
      description: "Optional path to a git repository. Defaults to the server's configured repo.",
    };
    return [
      {
        name: "summarize_repository",
        description: "Return repository metadata, working tree state, and top contributors.",
        inputSchema: {
          type: "object",
          properties: {
            repo_path: repoPathProperty,
          },
        },
      },
      {
        name: "get_commit_history",
        description: "Return commit history with optional author, path, and time filters.",
        inputSchema: {
          type: "object",
          properties: {
            repo_path: repoPathProperty,
            ref: { type: "string", default: "HEAD" },
            limit: { type: "integer", default: 20, minimum: 1, maximum: 200 },
            author: { type: "string" },
            path: { type: "string" },
            since: { type: "string" },
            until: { type: "string" },
          },
        },
      },
      {
        name: "rank_hotspots",
        description: "Rank files by recent churn.",
        inputSchema: {
          type: "object",
          properties: {
            repo_path: repoPathProperty,
            since_days: { type: "integer", default: 90, minimum: 1, maximum: 3650 },
            limit: { type: "integer", default: 10, minimum: 1, maximum: 200 },
            path_prefix: { type: "string" },
          },
        },
      },
      {
        name: "identify_owners",
        description: "Match paths against CODEOWNERS and git contributor history.",
        inputSchema: {
          type: "object",
          properties: {
            repo_path: repoPathProperty,
            paths: {
              type: "array",
              items: { type: "string" },
              minItems: 1,
            },
            max_contributors: { type: "integer", default: 3, minimum: 1, maximum: 20 },
          },
          required: ["paths"],
        },
      },
      {
        name: "inspect_ci",
        description: "Inspect local workflows and best-effort recent GitHub Actions runs.",
        inputSchema: {
          type: "object",
          properties: {
            repo_path: repoPathProperty,
            include_remote_runs: { type: "boolean", default: true },
          },
        },
      },
    ];
  }

  async #callTool(name, argumentsObject) {
    if (!argumentsObject || typeof argumentsObject !== "object" || Array.isArray(argumentsObject)) {
      return toolError("Tool arguments must be an object");
    }

    try {
      let data;
      if (name === "summarize_repository") {
        data = this.#analyzerForRepo(argumentsObject.repo_path).summary();
      } else if (name === "get_commit_history") {
        data = this.#analyzerForRepo(argumentsObject.repo_path).commitHistory({
          ref: String(argumentsObject.ref ?? "HEAD"),
          limit: this.#coerceInt(argumentsObject.limit, 20),
          author: this.#coerceOptionalString(argumentsObject.author),
          path: this.#coerceOptionalString(argumentsObject.path),
          since: this.#coerceOptionalString(argumentsObject.since),
          until: this.#coerceOptionalString(argumentsObject.until),
        });
        data.returned_commits = data.commits.length;
      } else if (name === "rank_hotspots") {
        data = this.#analyzerForRepo(argumentsObject.repo_path).rankHotspots({
          sinceDays: this.#coerceInt(argumentsObject.since_days, 90),
          limit: this.#coerceInt(argumentsObject.limit, 10),
          pathPrefix: this.#coerceOptionalString(argumentsObject.path_prefix),
        });
      } else if (name === "identify_owners") {
        const paths = argumentsObject.paths;
        if (!Array.isArray(paths) || paths.length === 0 || paths.some((item) => typeof item !== "string")) {
          return toolError("identify_owners requires a non-empty string array in paths");
        }
        data = this.#analyzerForRepo(argumentsObject.repo_path).identifyOwners(paths, {
          maxContributors: this.#coerceInt(argumentsObject.max_contributors, 3),
        });
      } else if (name === "inspect_ci") {
        data = await this.#analyzerForRepo(argumentsObject.repo_path).inspectCi({
          includeRemoteRuns: Boolean(argumentsObject.include_remote_runs ?? true),
        });
      } else {
        return toolError(`Unknown tool: ${name}`);
      }

      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
        structuredContent: data,
        isError: false,
      };
    } catch (error) {
      return toolError(String(error.message ?? error));
    }
  }

  #analyzerForRepo(repoPath) {
    if (repoPath) {
      return new GitActivityAnalyzer(repoPath);
    }
    if (!this.defaultRepoRoot) {
      throw new GitActivityError("No default repository configured. Pass repo_path or start the server with --repo.");
    }
    return new GitActivityAnalyzer(this.defaultRepoRoot);
  }

  #queryValue(parsedUrl, key) {
    const value = parsedUrl.searchParams.get(key);
    return value === null || value === "" ? null : value;
  }

  #queryInt(parsedUrl, key, defaultValue) {
    const value = this.#queryValue(parsedUrl, key);
    return value === null ? defaultValue : Number.parseInt(value, 10);
  }

  #coerceOptionalString(value) {
    if (value === undefined || value === null) {
      return null;
    }
    const text = String(value).trim();
    return text || null;
  }

  #coerceInt(value, defaultValue) {
    if (value === undefined || value === null || value === "") {
      return defaultValue;
    }
    return Number.parseInt(String(value), 10);
  }
}
