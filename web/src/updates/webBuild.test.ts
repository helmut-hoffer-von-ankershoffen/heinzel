import { describe, expect, it, vi } from "vitest";
import { entrypointsOf, webBuildOf } from "./webBuild";

// The same page and id as tests/unit/modules/system_about_test.py: the page and `hz system about` agree.
const PAGE = `<!doctype html><html><head>
  <script>paintSplash()</script>
  <script type="module" src="/assets/index-abc.js"></script>
  <link rel="modulepreload" href="/assets/vendor-123.js">
  <link rel="stylesheet" href="/assets/index-abc.css">
  <link rel="stylesheet" href="https://elsewhere.example/assets/x.css">
  <script src="/plugin-sdk/v1/react.js"></script>
</head><body></body></html>`;
const BUILD = "d24e6866a6e1";

const parse = (html: string) => new DOMParser().parseFromString(html, "text/html");

describe("web build", () => {
  it("is the hashed same-origin scripts and stylesheets a page loads, sorted, each once", () => {
    expect(entrypointsOf(parse(PAGE))).toEqual(["/assets/index-abc.css", "/assets/index-abc.js"]);
    expect(entrypointsOf(parse(`${PAGE}<script src="/assets/index-abc.js"></script>`))).toHaveLength(2);
  });

  it("has the id hz system about prints for the same page", async () => {
    expect(await webBuildOf(entrypointsOf(parse(PAGE)))).toBe(BUILD);
  });

  it("has no id without entrypoints (a dev server) or without Web Crypto (plain http)", async () => {
    expect(await webBuildOf([])).toBeNull();
    vi.stubGlobal("crypto", {});
    try {
      expect(await webBuildOf(["/assets/index-abc.js"])).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
