// U3: a click never types a command into the terminal behind the person's back. The terminal's sender may be reached
// from one place only, `components/CommandActions.tsx`, where a button says "Run in terminal" or the app runs the
// operation itself; this fails when another file starts typing into the terminal.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const ALLOWED = new Set(["components/CommandActions.tsx", "lib/terminal.ts", "layout/TerminalPanel.tsx"]);

function sources(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

it("lets only the labeled command button hand a command to the terminal", () => {
  const offenders = sources(ROOT)
    .map((path) => ({ file: relative(ROOT, path), text: readFileSync(path, "utf8") }))
    .filter(({ file, text }) => !ALLOWED.has(file) && /\b(runInTerminal|typeInTerminal)\b/.test(text))
    .map(({ file }) => file);
  expect(offenders).toEqual([]);
});

it("never presses Enter for a contributed action on its own", () => {
  const text = readFileSync(join(ROOT, "components/Contributions.tsx"), "utf8");
  expect(text).not.toMatch(/runsAsIs|runInTerminal|typeInTerminal/);
});
