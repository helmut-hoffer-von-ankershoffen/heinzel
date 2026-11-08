// The build's stamp on the service worker (public/sw.js, ADR 0046): `const BUILD = "dev"` becomes a hash of every
// other file the build wrote. A new build is then a new worker, so the browser installs it and its shell cache, named
// after the build, replaces the last one; an unchanged build keeps the same worker. Node only: vite.config.ts uses it.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import type { Plugin } from "vite";

export const WORKER = "sw.js";
export const PLACEHOLDER = 'const BUILD = "dev";';
export const BUILD_PATTERN = /^const BUILD = "([0-9a-f]{16})";$/m;

function files(root: string, dir = root): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return files(root, path);
    return entry.isFile() ? [relative(root, path).split(sep).join("/")] : [];
  });
}

/** The build's id: a hash of each file's path and bytes, the worker's own excepted, in a fixed order. */
export function buildId(outDir: string): string {
  const hash = createHash("sha256");
  for (const name of files(outDir)
    .filter((name) => name !== WORKER)
    .sort()) {
    hash
      .update(name)
      .update("\0")
      .update(readFileSync(join(outDir, name)))
      .update("\0");
  }
  return hash.digest("hex").slice(0, 16);
}

/** Stamp the worker in `outDir` with the build's id; a worker without the placeholder fails the build. */
export function stampWorker(outDir: string): string {
  const path = join(outDir, WORKER);
  const source = readFileSync(path, "utf8");
  if (source.split(PLACEHOLDER).length !== 2) {
    throw new Error(`${WORKER} must contain ${PLACEHOLDER} exactly once: the build stamps its id there`);
  }
  const id = buildId(outDir);
  writeFileSync(path, source.replace(PLACEHOLDER, `const BUILD = "${id}";`));
  return id;
}

/** The Vite plugin: stamps the worker once the build has written every file. */
export function shellBuild(): Plugin {
  let outDir = "";
  return {
    name: "hz-shell-build",
    apply: "build",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir); // the build script passes an absolute one
    },
    closeBundle() {
      const id = stampWorker(outDir);
      this.info?.(`web.shell.build version=${id}`);
    },
  };
}
