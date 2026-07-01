import { GitActivityMCPServer } from "./node_impl/server.mjs";

function parseArgs(argv) {
  let repo = null;
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--repo") {
      repo = argv[index + 1] ?? null;
      index += 1;
    }
  }
  return { repo };
}

const { repo } = parseArgs(process.argv.slice(2));
await new GitActivityMCPServer(repo).runStdio();
