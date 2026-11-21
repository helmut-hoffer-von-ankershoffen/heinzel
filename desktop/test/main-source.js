import { readFileSync } from "node:fs";

// The main process is split by concern into main-*.js files. These tests read it as one script: the files in
// dependency order, with the module syntax (imports, exports) removed.
const FILES = [
  "main-state.js", "main-picker.js", "main-services.js", "main-window.js", "main-controller.js", "main-ipc.js",
  "main-service-watch.js", "main-quit.js", "main-menu.js", "main-startup.js", "main.js",
];

/** The text of the whole main process, as it was one file before the split. */
export function mainSource() {
  return FILES.map((name) => readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8")).join("\n");
}

/** The same text as a script that a vm can run: no imports or exports, `import.meta.url` replaced by `moduleUrl`. */
export function mainScript() {
  return mainSource().replace(/^import .*;\n/gm, "").replace(/^export /gm, "").replaceAll("import.meta.url", "moduleUrl");
}

/** One of the files of the main process, by name (e.g. "main-startup.js"). */
export function sourceOf(name) {
  return readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8");
}
