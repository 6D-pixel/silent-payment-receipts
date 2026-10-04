// Loads Pyodide and the unchanged Python receipt code, then exposes one call() into webdemo/bridge.py.

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.29.5/full/pyodide.mjs";
const APP_DIR = "/home/pyodide/app";

interface Pyodide {
  FS: { mkdirTree(path: string): void; writeFile(path: string, data: string): void };
  runPython(code: string): unknown;
  pyimport(name: string): { call(method: string, args: string): string };
}

let bridge: { call(method: string, args: string): string } | null = null;

export async function loadPython(onProgress: (message: string) => void): Promise<void> {
  onProgress("Loading Python into your browser…");
  const { loadPyodide } = await import(/* @vite-ignore */ PYODIDE_URL);
  const pyodide: Pyodide = await loadPyodide();

  onProgress("Loading the receipt code…");
  const base = new URL("py/", document.baseURI);
  const files: string[] = await (await fetch(new URL("manifest.json", base))).json();
  await Promise.all(files.map(async (file) => {
    const text = await (await fetch(new URL(file, base))).text();
    const path = `${APP_DIR}/${file}`;
    pyodide.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
    pyodide.FS.writeFile(path, text);
  }));
  pyodide.runPython(`import sys; sys.path.insert(0, ${JSON.stringify(APP_DIR)})`);
  bridge = pyodide.pyimport("webdemo.bridge");
}

export class PyError extends Error {}

/** Call a DemoSession method in Python. Throws PyError with Python's message on failure. */
export function call<T>(method: string, args: object = {}): T {
  if (!bridge) throw new PyError("The receipt code has not loaded yet.");
  const reply = JSON.parse(bridge.call(method, JSON.stringify(args)));
  if (!reply.ok) throw new PyError(reply.error);
  return reply.result as T;
}
