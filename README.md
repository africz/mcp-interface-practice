# MCP Interface Practice

This project implements a small MCP server for a Git Activity Analyzer.

## Files

- `docs/interface.md` contains the interface design
- `server.py` starts the MCP server over stdio
- `server.mjs` starts the Node.js MCP server over stdio
- `git_activity_analyzer/` contains the repository analysis and protocol code
- `node_impl/` contains the Node.js analysis and protocol code
- `tests/` contains a small unit test suite
- `node_tests/` contains the Node.js unit test suite

## Run

```bash
python3 server.py --repo ../agent-adding-functionality
```

```bash
node server.mjs --repo ../agent-adding-functionality
```

The server speaks newline-delimited JSON-RPC on stdin/stdout.

## Test

```bash
python3 -m unittest discover -s tests -v
```

```bash
npm test
```
