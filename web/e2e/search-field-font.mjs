// Measure the actual Settings search field at its first focus, with compact and enlarged UI font tokens.
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";

const [url, shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const cases = [];
for (const [engine, launcher] of [
  ["chromium", chromium],
  ["webkit", webkit],
]) {
  const browser = await launcher.launch();
  try {
    for (const touch of [false, true]) {
      for (const themeSize of [12, 22]) {
        const context = await browser.newContext({
          viewport: { width: 430, height: 932 },
          isMobile: true,
          hasTouch: touch,
          serviceWorkers: "block",
        });
        const page = await context.newPage();
        await page.addInitScript((size) => {
          window.searchFontFocus = [];
          const apply = () => document.documentElement?.style.setProperty("--text-ui", `${size}px`);
          apply();
          const rootReady = new MutationObserver(() => {
            if (document.documentElement) {
              apply();
              rootReady.disconnect();
            }
          });
          if (!document.documentElement) rootReady.observe(document, { childList: true, subtree: true });
          document.addEventListener(
            "focus",
            (event) => {
              if (event.target instanceof HTMLInputElement && event.target.type === "search")
                window.searchFontFocus.push(Number.parseFloat(getComputedStyle(event.target).fontSize));
            },
            true,
          );
        }, themeSize);
        const target = new URL(url);
        target.searchParams.set("settings", "hz");
        await page.goto(target.href);
        const field = page.getByRole("searchbox", { name: "Filter settings" });
        await field.waitFor();
        await page.locator("#splash").waitFor({ state: "hidden" });
        await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"));
        const facts = await field.evaluate((element) => ({
          fontSize: Number.parseFloat(getComputedStyle(element).fontSize),
          uiSize: getComputedStyle(element).getPropertyValue("--text-ui"),
          coarse: matchMedia("(pointer: coarse)").matches,
          anyCoarse: matchMedia("(any-pointer: coarse)").matches,
          width: innerWidth,
          firstFocus: window.searchFontFocus,
        }));
        cases.push({ engine, touch, themeSize, ...facts });
        writeFileSync(join(shots, "search-field-font.json"), `${JSON.stringify(cases, null, 2)}\n`);
        await page.screenshot({ path: join(shots, `search-font-${engine}-${touch}-${themeSize}.png`) });
        console.error(`SEARCH_FIELD_FONT:${JSON.stringify(cases.at(-1))}`);
        assert.ok(facts.fontSize >= 16, "Phone search text must be at least16px before interaction");
        assert.ok(facts.fontSize >= themeSize, "The minimum must preserve enlarged UI text");
        await field.focus();
        const focusFonts = await page.evaluate(() => window.searchFontFocus);
        assert.ok(
          focusFonts.length > 0 && focusFonts.every((size) => size >= 16),
          "Initial/autofocus text must already be at least16px; waiting later cannot prevent zoom",
        );
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
}
console.log(JSON.stringify({ cases }));
