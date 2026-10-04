// Copy the Python receipt code (and the real regtest samples) into public/py/ with a manifest,
// so the page can load it into Pyodide. The Python files in the repo stay the single source.
import { cpSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

const repo = new URL("../..", import.meta.url).pathname;
const out = new URL("../public/py", import.meta.url).pathname;
const wanted = [
  ["spreceipt", (f) => f.endsWith(".py")],
  ["webdemo", (f) => f.endsWith(".py")],
  ["demo/samples", (f) => f.endsWith(".json")],
];

rmSync(out, { recursive: true, force: true });
const files = [];
function walk(dir, keep) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (name === "__pycache__") continue;
    if (statSync(path).isDirectory()) walk(path, keep);
    else if (keep(name)) files.push(relative(repo, path));
  }
}
for (const [dir, keep] of wanted) walk(join(repo, dir), keep);
for (const f of files) {
  mkdirSync(join(out, f, ".."), { recursive: true });
  cpSync(join(repo, f), join(out, f));
}
writeFileSync(join(out, "manifest.json"), JSON.stringify(files.sort(), null, 1));
console.log(`copied ${files.length} files to public/py`);
