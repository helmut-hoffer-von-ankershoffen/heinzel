// A feature owns a directory: `features/<name>` imports the kit (api, components, lib, i18n, app) and itself, never
// another feature or the shell. Today's imports of other parts of the app are listed; the list only shrinks, and an
// entry that no longer matches an import fails so it is removed.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
const FEATURES = join(SRC, "features");
const KIT = new Set(["api", "components", "lib", "i18n", "app", "test"]);

/** Imports that reach outside the feature and the kit today, as "file -> top-level directory of src". */
const ALLOWED = new Set([
  "features/runtime/LifecycleSection.test.tsx -> heinzel",
  "features/runtime/LifecycleSection.tsx -> heinzel",
  "features/runtime/UpdateStatus.test.tsx -> about",
  "features/runtime/UpdateStatus.tsx -> about",
  "features/runtime/updates/AutoUpdate.test.tsx -> chat",
  "features/runtime/updates/AutoUpdateToggle.tsx -> layout",
  "features/runtime/updates/UpdatePlanDetails.tsx -> layout",
]);

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

function targets(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const found: string[] = [];
  for (const match of text.matchAll(/["'](@\/[^"']+|\.\.?\/[^"']*)["']/g)) {
    const spec = match[1] as string;
    const path = spec.startsWith("@/") ? join(SRC, spec.slice(2)) : resolve(join(file, ".."), spec);
    found.push(relative(SRC, path).split(sep)[0] as string);
  }
  return found;
}

function featureOf(file: string): string {
  return relative(FEATURES, file).split(sep)[0] as string;
}

describe("features", () => {
  const seen = new Set<string>();
  const offending = new Set<string>();
  for (const file of files(FEATURES).filter((f) => /\.(ts|tsx)$/.test(f))) {
    const own = featureOf(file);
    const text = readFileSync(file, "utf8");
    for (const match of text.matchAll(/["']@\/features\/([^/"']+)/g)) {
      if (match[1] !== own) offending.add(`${relative(SRC, file)} -> features/${match[1]}`);
    }
    for (const top of targets(file).filter((t) => t !== "features" && !KIT.has(t))) {
      const key = `${relative(SRC, file).split(sep).join("/")} -> ${top}`;
      seen.add(key);
      if (!ALLOWED.has(key)) offending.add(key);
    }
  }

  it("never reaches another feature or the shell beyond the listed imports", () => {
    expect([...offending].sort()).toEqual([]);
  });

  it("lists only imports that still exist", () => {
    expect([...ALLOWED].filter((entry) => !seen.has(entry))).toEqual([]);
  });
});
