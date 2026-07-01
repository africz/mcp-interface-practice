from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from git_activity_analyzer import GitActivityAnalyzer, GitActivityMCPServer


class GitActivityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.tempdir.name)
        self._create_repo()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_summary_and_hotspots(self) -> None:
        analyzer = GitActivityAnalyzer(self.repo_path)

        summary = analyzer.summary()
        self.assertEqual(summary["current_branch"], "main")
        self.assertEqual(summary["commit_count"], 3)
        self.assertEqual(summary["repo_name"], self.repo_path.name)
        self.assertTrue(summary["working_tree"]["clean"])

        hotspots = analyzer.rank_hotspots(since_days=365, limit=5)
        self.assertGreaterEqual(hotspots["returned_hotspots"], 2)
        self.assertEqual(hotspots["hotspots"][0]["path"], "src/app.py")
        self.assertEqual(hotspots["hotspots"][0]["touches"], 3)

    def test_ownership_and_ci(self) -> None:
        analyzer = GitActivityAnalyzer(self.repo_path)

        owners = analyzer.identify_owners(["src/app.py", "README.md"])
        owner_map = {entry["path"]: entry for entry in owners["results"]}
        self.assertEqual(owner_map["src/app.py"]["codeowners"]["owners"], ["@backend-team"])
        self.assertEqual(owner_map["README.md"]["codeowners"]["owners"], ["@docs-team"])
        self.assertEqual(owner_map["src/app.py"]["recent_contributors"][0]["author"], "Alice")

        ci = analyzer.inspect_ci(include_remote_runs=False)
        self.assertTrue(ci["ci_configured"])
        self.assertEqual(ci["workflows"][0]["name"], "CI")
        self.assertIn("push", ci["workflows"][0]["triggers"])
        self.assertEqual(ci["remote_ci"]["status"], "skipped")

    def test_protocol_surface(self) -> None:
        server = GitActivityMCPServer(default_repo_path=self.repo_path)

        init_response = server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(init_response["result"]["serverInfo"]["name"], "git-activity-analyzer")

        resource_response = server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "git-activity://summary"}}
        )
        resource_payload = json.loads(resource_response["result"]["contents"][0]["text"])
        self.assertEqual(resource_payload["repo_root"], str(self.repo_path.resolve()))

        tool_response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "identify_owners",
                    "arguments": {"paths": ["src/app.py"], "max_contributors": 2},
                },
            }
        )
        self.assertFalse(tool_response["result"]["isError"])
        structured = tool_response["result"]["structuredContent"]
        self.assertEqual(structured["results"][0]["primary_owner"], "@backend-team")

    def _create_repo(self) -> None:
        self._run("git", "init", "-b", "main")
        self._run("git", "config", "user.name", "Test User")
        self._run("git", "config", "user.email", "test@example.com")
        self._run("git", "remote", "add", "origin", "https://github.com/example/git-activity-analyzer.git")

        (self.repo_path / ".github" / "workflows").mkdir(parents=True)
        (self.repo_path / "src").mkdir()

        (self.repo_path / ".github" / "workflows" / "ci.yml").write_text(
            "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
            encoding="utf-8",
        )
        (self.repo_path / "CODEOWNERS").write_text(
            "# Ownership rules\n/src/ @backend-team\nREADME.md @docs-team\n",
            encoding="utf-8",
        )
        (self.repo_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
        self._commit("Initial commit", "Alice", "alice@example.com")

        (self.repo_path / "src" / "app.py").write_text(
            "print('hello')\nprint('feature')\n",
            encoding="utf-8",
        )
        (self.repo_path / "src" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        self._commit("Add util helper", "Bob", "bob@example.com")

        (self.repo_path / "src" / "app.py").write_text(
            "print('hello')\nprint('feature')\nprint('refined')\n",
            encoding="utf-8",
        )
        (self.repo_path / "README.md").write_text("# Demo repo\n", encoding="utf-8")
        self._commit("Refine app and docs", "Carol", "carol@example.com")

    def _commit(self, message: str, author_name: str, author_email: str) -> None:
        self._run("git", "add", ".")
        self._run(
            "git",
            "commit",
            "--author",
            f"{author_name} <{author_email}>",
            "-m",
            message,
        )

    def _run(self, *args: str) -> None:
        subprocess.run(args, cwd=self.repo_path, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
