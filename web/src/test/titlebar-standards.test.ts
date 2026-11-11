// Standards of the desktop app's integrated title bar (design/README.md, "Integrated title bar") that one component
// test cannot see:
// - Only the row's background drags: every selector index.css makes `drag` is listed in the one rule that gives the
//   interactive elements inside it `no-drag`, and no component sets an app region of its own.
// - A pane over the window (a full-height overlay anchored to the window's top) starts under the title bar row, so its
//   header never meets the traffic lights or the drag region, and it claims no-drag (data-pane).
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
const css = readFileSync(join(SRC, "index.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const PREFIX = "html[data-desktop-titlebar]";

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "test" ? [] : sources(path);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

/** Splits at commas outside parentheses. */
function list(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of text) {
    if (char === "(") depth += 1;
    if (char === ")") depth -= 1;
    if (char === "," && depth === 0) {
      parts.push(current);
      current = "";
    } else current += char;
  }
  return [...parts, current].map((part) => part.replace(/\s+/g, " ").trim()).filter(Boolean);
}

const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
  selectors: list(match[1] ?? ""),
  body: match[2] ?? "",
}));

/** The argument of the :is( that starts at `start`, with nested parentheses. */
function isArgument(selector: string, start: number): [string, number] {
  let depth = 0;
  for (let index = start + 3; index < selector.length; index += 1) {
    if (selector[index] === "(") depth += 1;
    if (selector[index] === ")") {
      depth -= 1;
      if (depth === 0) return [selector.slice(start + 4, index), index + 1];
    }
  }
  throw new Error(`Unclosed :is( in ${selector}`);
}

describe("integrated title bar", () => {
  it("lets only the row's background drag: every drag selector's controls are no-drag", () => {
    const drags = rules
      .filter((rule) => /-webkit-app-region:\s*drag\b/.test(rule.body))
      .flatMap((rule) => rule.selectors)
      .map((selector) => {
        expect(selector.startsWith(PREFIX), `${selector} is scoped to the desktop app`).toBe(true);
        return selector.slice(PREFIX.length).trim();
      });
    expect(drags.length).toBeGreaterThan(0);
    const exemptions = rules.filter(
      (rule) => /-webkit-app-region:\s*no-drag\b/.test(rule.body) && rule.selectors.some((s) => s.includes(":is(")),
    );
    const covered = exemptions.flatMap((rule) =>
      rule.selectors.flatMap((selector) => {
        const first = selector.indexOf(":is(");
        const [segments, end] = isArgument(selector, first);
        const second = selector.indexOf(":is(", end);
        if (second < 0) return [];
        const [controls] = isArgument(selector, second);
        return list(segments).map((segment) => ({ segment, controls: list(controls) }));
      }),
    );
    const interactive = ["a", "button", "input", "select", "textarea", '[role="tab"]', '[role="menuitem"]'];
    for (const drag of drags) {
      const match = covered.find((entry) => entry.segment === drag);
      expect(match, `${drag} drags, but nothing inside it is made no-drag`).toBeDefined();
      expect(match?.controls).toEqual(expect.arrayContaining(interactive));
    }
  });

  it("declares app regions only in index.css, never in a component", () => {
    const found = sources(SRC).flatMap((path) =>
      /app-region|WebkitAppRegion/.test(readFileSync(path, "utf8")) ? [relative(SRC, path)] : [],
    );
    expect(found).toEqual([]);
  });

  it("starts every pane anchored to the window's top under the title bar row, and marks it no-drag", () => {
    const anchored = /(?:absolute|fixed)\s[^"]*top-\[var\(--hz-safe-area-top/;
    const panes = sources(SRC).flatMap((path) => {
      const text = readFileSync(path, "utf8");
      return [...text.matchAll(/"[^"]*"/g)]
        .map((match) => match[0])
        .filter((value) => anchored.test(value) || /top-\[calc\(var\(--hz-safe-area-top/.test(value))
        .map((value) => ({ path: relative(SRC, path), value, text }));
    });
    expect(panes.length).toBeGreaterThan(0);
    for (const pane of panes) {
      expect(pane.value, `${pane.path} reserves the title bar row`).toMatch(
        /top-\[calc\(var\(--hz-safe-area-top[^\]]*\+var\(--titlebar-height,0px\)\)\]/,
      );
      expect(pane.text, `${pane.path} marks its pane no-drag`).toMatch(/\bdata-pane\b/);
    }
    const exemption = rules.find(
      (rule) => /-webkit-app-region:\s*no-drag\b/.test(rule.body) && rule.selectors.join().includes("[data-pane]"),
    );
    expect(exemption, "index.css makes [data-pane] no-drag").toBeDefined();
  });
});
