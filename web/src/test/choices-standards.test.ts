// Standards from design/README.md that a component test cannot see one screen at a time:
// - Choices: an exclusive choice is a radiogroup of radios (`aria-checked`), never a set of `aria-pressed` buttons;
//   `aria-pressed` is for one independent on/off toggle.
// - Visualizer colours: the spectrum and aurora take every hue from the live theme tokens, never a written colour.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

function sources(dir: string, extensions: RegExp): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "test" ? [] : sources(path, extensions);
    return extensions.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

const withoutComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

describe("choices", () => {
  it("never marks one option of an exclusive set with aria-pressed", () => {
    const found = sources(SRC, /\.tsx$/).flatMap((path) => {
      const text = withoutComments(readFileSync(path, "utf8"));
      // `aria-pressed={kind === value}` is a member of a set; a role="radio" must carry aria-checked instead.
      const equality = [...text.matchAll(/aria-pressed=\{[^}]*[!=]==[^}]*\}/g)].map((match) => match[0]);
      const radio = /role="radio"[^>]*aria-pressed|aria-pressed[^>]*role="radio"/.test(text) ? ["radio+pressed"] : [];
      return [...equality, ...radio].map((hit) => `${relative(SRC, path)}: ${hit}`);
    });
    expect(found).toEqual([]);
  });
});

describe("visualizer colours", () => {
  it("writes no colour in the spectrum or aurora: every hue is a theme token", () => {
    const literal =
      /["'`]#[0-9a-f]{3,8}["'`]|\brgba?\(\s*\d|\bhsla?\(\s*\d|\boklch\(\s*[\d.]|\{\s*r:\s*[1-9]\d*\s*,\s*g:/gi;
    const found = sources(join(SRC, "audio"), /\.tsx?$/).flatMap((path) =>
      [...withoutComments(readFileSync(path, "utf8")).matchAll(literal)].map(
        (match) => `${relative(SRC, path)}: ${match[0]}`,
      ),
    );
    expect(found).toEqual([]);
  });
});

describe("visualizer layering", () => {
  it("paints the page colour on <html>, never on <body>, so the aurora under the content is seen", () => {
    // index.html gives <html> the splash colour; a painted <body> then covers every negative layer, the aurora too.
    const css = withoutComments(readFileSync(join(SRC, "index.css"), "utf8"));
    const body = [...css.matchAll(/(^|\n)body\s*\{([^}]*)\}/g)].map((match) => match[2] ?? "").join("\n");
    expect(body).toMatch(/background:\s*transparent/);
    expect(css).toMatch(/(^|\n)html\s*\{[^}]*background:\s*var\(--color-background\)/);
  });
});
