import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { acmeTheme, theme } from "@/test/fixtures";
import { readSnapshot, remember, SNAPSHOT_KEY, themeSnapshot } from "./snapshot";

const page = readFileSync(join(process.cwd(), "index.html"), "utf8"); // vitest runs in web/;
const markup = /<div id="splash"[\s\S]*?<\/div>\s*(?=<script>)/.exec(page)?.[0] ?? "";
const script = /<script>([\s\S]*?)<\/script>/.exec(page)?.[1] ?? "";

/** The splash as the browser paints it from index.html: its markup, then its inline script. */
function paint() {
  document.body.innerHTML = `${markup}<div id="root"></div>`;
  document.head.innerHTML =
    '<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">' +
    '<meta name="theme-color" content="#181818" media="(prefers-color-scheme: dark)">';
  new Function(script)();
}

/** localStorage as a browser with site data blocked has it: reading the property throws. */
function blockStorage(): () => void {
  const own = Object.getOwnPropertyDescriptor(window, "localStorage");
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() {
      throw new DOMException("The operation is insecure.", "SecurityError");
    },
  });
  return () => {
    if (own) Object.defineProperty(window, "localStorage", own);
    else delete (window as { localStorage?: Storage }).localStorage;
  };
}

afterEach(() => {
  document.documentElement.className = "";
  document.documentElement.removeAttribute("style");
  document.body.innerHTML = "";
});

describe("index.html's splash", () => {
  it("paints the default theme's branding and the starting line before React loads", () => {
    paint();
    expect(document.getElementById("splash-brand")?.textContent).toContain("Heinzelmännchen");
    const line = document.getElementById("splash-line");
    expect(line?.textContent).toBe("Starting…");
    expect(line).toHaveAttribute("role", "status");
    expect(line).toHaveAttribute("aria-live", "polite");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("paints the last theme, mode, logo and version the app remembered", () => {
    localStorage.setItem("hz.theme", "dark");
    remember({ ...themeSnapshot(acmeTheme), version: "0.9.1" });
    paint();
    const root = document.documentElement;
    expect(root.classList.contains("dark")).toBe(true);
    expect(root.style.colorScheme).toBe("dark");
    expect(root.style.getPropertyValue("--splash-background")).toBe(acmeTheme.dark.background);
    expect(root.style.getPropertyValue("--splash-primary")).toBe(acmeTheme.dark.primary);
    const logo = document.querySelector("#splash-brand img");
    expect(logo).toHaveAttribute("src", acmeTheme.logo?.dark);
    expect(logo).toHaveAttribute("alt", acmeTheme.logo?.alt);
    expect(document.getElementById("splash-version")?.textContent).toBe("v0.9.1");
    for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
      expect(meta).toHaveAttribute("content", acmeTheme.dark.background);
    }
  });

  it("refuses remembered values that aren't a colour, a font stack, a logo of this origin or a version", () => {
    localStorage.setItem(
      SNAPSHOT_KEY,
      JSON.stringify({
        light: { background: "red;}body{display:none", primary: "url(https://evil.example.test)" },
        font: "x;}html{background:url(https://evil.example.test)}",
        logo: { light: "https://evil.example.test/logo.svg", dark: "//evil.example.test/x.svg", alt: "Evil" },
        version: "<img src=x onerror=alert(1)>",
      }),
    );
    paint();
    const root = document.documentElement;
    expect(root.style.getPropertyValue("--splash-background")).toBe("");
    expect(root.style.getPropertyValue("--splash-primary")).toBe("");
    expect(root.style.getPropertyValue("--splash-font")).toBe("");
    expect(document.querySelector("#splash-brand img")).toBeNull();
    expect(document.getElementById("splash-version")?.textContent).toBe("");
  });

  it("still paints, in the default theme, with storage disabled or a damaged snapshot", () => {
    localStorage.setItem(SNAPSHOT_KEY, "{not json");
    paint();
    expect(document.getElementById("splash-line")?.textContent).toBe("Starting…");
    const restore = blockStorage();
    try {
      expect(() => paint()).not.toThrow();
      expect(document.getElementById("splash-brand")?.textContent).toContain("Heinzelmännchen");
      expect(readSnapshot()).toEqual({});
      expect(() => remember({ version: "1.0.0" })).not.toThrow();
    } finally {
      restore();
    }
  });

  it("holds the progress still for someone who asked for less motion", () => {
    expect(page).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*#splash \{\s*transition: none;/);
    expect(page).toMatch(/#splash-bar::after \{\s*width: 100%;\s*opacity: 0.6;\s*animation: none;/);
  });
});

describe("the snapshot", () => {
  it("keeps what a theme decides, and nothing for the built-in theme, whose look is index.html's", () => {
    remember({ ...themeSnapshot(acmeTheme), version: "1.2.3" });
    expect(readSnapshot().light?.background).toBe(acmeTheme.light.background);
    expect(readSnapshot().logo?.alt).toBe(acmeTheme.logo?.alt);
    remember(themeSnapshot(theme()));
    expect(readSnapshot()).toEqual({ version: "1.2.3" });
    localStorage.setItem(SNAPSHOT_KEY, "[1]");
    expect(readSnapshot()).toEqual({});
  });
});
