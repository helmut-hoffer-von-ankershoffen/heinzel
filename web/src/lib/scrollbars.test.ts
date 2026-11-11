// Scrollbars stay thin, transparent-tracked and auto-hiding everywhere; the contract lives in the shared stylesheet.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

// Vitest blanks processed CSS imports, so read the source file (tests run from web/).
const css = readFileSync(join(process.cwd(), "src/index.css"), "utf8");

it("makes every scroll area thin with a transparent track and a thumb shown only on hover or focus", () => {
  expect(css).toMatch(/:where\(\*\)\s*{[^}]*scrollbar-width:\s*thin;[^}]*scrollbar-color:\s*transparent transparent;/);
  expect(css).toMatch(
    /:where\(\*:hover, \*:focus-within\)\s*{[^}]*scrollbar-color:\s*color-mix\([^}]*var\(--color-muted-foreground\)[^}]*\)\s*transparent;/,
  );
});

it("keeps a WebKit fallback with a transparent track and corner", () => {
  expect(css).toMatch(/@supports not \(scrollbar-width: thin\)/);
  expect(css).toMatch(/::-webkit-scrollbar-track,\s*\*::-webkit-scrollbar-corner\s*{\s*background:\s*transparent;/);
  expect(css).toMatch(/:hover::-webkit-scrollbar-thumb/);
});

it("does not hide scrolling with overflow or by removing the scrollbar", () => {
  expect(css).not.toMatch(/scrollbar-width:\s*none/);
  expect(css).not.toMatch(/::-webkit-scrollbar\s*{[^}]*display:\s*none/);
});
