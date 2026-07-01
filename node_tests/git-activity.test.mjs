import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { execFileSync } from "node:child_process";

import { GitActivityAnalyzer } from "../node_impl/analyzer.mjs";
import { GitActivityMCPServer } from "../node_impl/server.mjs";

function run(cwd, ...args) {
  return execFileSync(args[0], args.slice(1), {
    cwd,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
}

function commit(repoPath, message, authorName, authorEmail) {
  run(repoPath, "git", "add", ".");
  run(repoPath, "git", "commit", "--author", `${authorName} <${authorEmail}>`, "-m", message);
}

function createRepo() {
  const repoPath = fs.mkdtempSync(path.join(os.tmpdir(), "mcp-node-test-"));
  run(repoPath, "git", "init", "-b", "main");
  run(repoPath, "git", "config", "user.name", "Test User");
  run(repoPath, "git", "config", "user.email", "test@example.com");
  run(repoPath, "git", "remote", "add", "origin", "https://github.com/example/git-activity-analyzer.git");

  fs.mkdirSync(path.join(repoPath, ".github", "workflows"), { recursive: true });
  fs.mkdirSync(path.join(repoPath, "src"), { recursive: true });

  fs.writeFileSync(
    path.join(repoPath, ".github", "workflows", "ci.yml"),
    "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
    "utf8"
  );
  fs.writeFileSync(
    path.join(repoPath, "CODEOWNERS"),
    "# Ownership rules\n/src/ @backend-team\nREADME.md @docs-team\n",
    "utf8"
  );
  fs.writeFileSync(path.join(repoPath, "src", "app.py"), "print('hello')\n", "utf8");
  commit(repoPath, "Initial commit", "Alice", "alice@example.com");

  fs.writeFileSync(path.join(repoPath, "src", "app.py"), "print('hello')\nprint('feature')\n", "utf8");
  fs.writeFileSync(path.join(repoPath, "src", "util.py"), "def helper():\n    return 1\n", "utf8");
  commit(repoPath, "Add util helper", "Bob", "bob@example.com");

  fs.writeFileSync(
    path.join(repoPath, "src", "app.py"),
    "print('hello')\nprint('feature')\nprint('refined')\n",
    "utf8"
  );
  fs.writeFileSync(path.join(repoPath, "README.md"), "# Demo repo\n", "utf8");
  commit(repoPath, "Refine app and docs", "Carol", "carol@example.com");

  return repoPath;
}

test("summary and hotspots", () => {
  const repoPath = createRepo();
  const analyzer = new GitActivityAnalyzer(repoPath);

  const summary = analyzer.summary();
  assert.equal(summary.current_branch, "main");
  assert.equal(summary.commit_count, 3);
  assert.equal(summary.repo_name, path.basename(repoPath));
  assert.equal(summary.working_tree.clean, true);

  const hotspots = analyzer.rankHotspots({ sinceDays: 365, limit: 5 });
  assert.ok(hotspots.returned_hotspots >= 2);
  assert.equal(hotspots.hotspots[0].path, "src/app.py");
  assert.equal(hotspots.hotspots[0].touches, 3);
});

test("ownership and ci", async () => {
  const repoPath = createRepo();
  const analyzer = new GitActivityAnalyzer(repoPath);

  const owners = analyzer.identifyOwners(["src/app.py", "README.md"]);
  const ownerMap = new Map(owners.results.map((entry) => [entry.path, entry]));
  assert.deepEqual(ownerMap.get("src/app.py").codeowners.owners, ["@backend-team"]);
  assert.deepEqual(ownerMap.get("README.md").codeowners.owners, ["@docs-team"]);
  assert.equal(ownerMap.get("src/app.py").recent_contributors[0].author, "Alice");

  const ci = await analyzer.inspectCi({ includeRemoteRuns: false });
  assert.equal(ci.ci_configured, true);
  assert.equal(ci.workflows[0].name, "CI");
  assert.ok(ci.workflows[0].triggers.includes("push"));
  assert.equal(ci.remote_ci.status, "skipped");
});

test("protocol surface", async () => {
  const repoPath = createRepo();
  const server = new GitActivityMCPServer(repoPath);

  const initResponse = await server.handleMessage({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {},
  });
  assert.equal(initResponse.result.serverInfo.name, "git-activity-analyzer");

  const resourceResponse = await server.handleMessage({
    jsonrpc: "2.0",
    id: 2,
    method: "resources/read",
    params: { uri: "git-activity://summary" },
  });
  const resourcePayload = JSON.parse(resourceResponse.result.contents[0].text);
  assert.equal(resourcePayload.repo_root, repoPath);

  const toolResponse = await server.handleMessage({
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: {
      name: "identify_owners",
      arguments: { paths: ["src/app.py"], max_contributors: 2 },
    },
  });
  assert.equal(toolResponse.result.isError, false);
  assert.equal(toolResponse.result.structuredContent.results[0].primary_owner, "@backend-team");
});
