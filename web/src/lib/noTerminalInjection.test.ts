// U3: the app never types a command into the terminal for the person. The terminal has no sender any more; this fails
// if a source file brings one back or opens the terminal on a command's behalf.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, it } from "vitest";

const ROOT = join(__dirname, "..");

function sources(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

it("has no way to hand a command to the terminal", () => {
  const offenders = sources(ROOT)
    .map((path) => ({ file: relative(ROOT, path), text: readFileSync(path, "utf8") }))
    .filter(({ text }) => /\b(runInTerminal|typeInTerminal|connectTerminal|OPEN_TERMINAL|runsAsIs)\b/.test(text))
    .map(({ file }) => file);
  expect(offenders).toEqual([]);
});

it("never presses Enter for a contributed action on its own", () => {
  const text = readFileSync(join(ROOT, "components/Contributions.tsx"), "utf8");
  expect(text).not.toMatch(/runsAsIs|runInTerminal|typeInTerminal/);
});
