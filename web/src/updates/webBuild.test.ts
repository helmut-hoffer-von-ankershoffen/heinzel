import { describe, expect, it } from "vitest";
import { entrypointsOf } from "./webBuild";

const PAGE = `<!doctype html><html><head>
  <script>paintSplash()</script>
  <script type="module" src="/assets/index-abc.js"></script>
  <link rel="modulepreload" href="/assets/vendor-123.js">
  <link rel="stylesheet" href="/assets/index-abc.css">
  <link rel="stylesheet" href="https://elsewhere.example/assets/x.css">
  <script src="/plugin-sdk/v1/react.js"></script>
</head><body></body></html>`;

const parse = (html: string) => new DOMParser().parseFromString(html, "text/html");

describe("web build", () => {
  it("is the hashed same-origin scripts and stylesheets a page loads, sorted, each once", () => {
    expect(entrypointsOf(parse(PAGE))).toEqual(["/assets/index-abc.css", "/assets/index-abc.js"]);
    expect(entrypointsOf(parse(`${PAGE}<script src="/assets/index-abc.js"></script>`))).toHaveLength(2);
  });
});
