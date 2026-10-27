// Actual mounted footer proof. Its synthetic provider exercises declarations; resources use the real backend.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { verifyLayers } from "./layers.mjs";

export function navigationFolded() {
  const sidebar = document.querySelector("aside");
  // The final animated pixel can make a hidden clock fit again between the geometry snapshot and More's click.
  return (
    sidebar.getBoundingClientRect().width <= 57 &&
    sidebar
      .getAnimations()
      .every((animation) => !animation.pending && !["running", "paused"].includes(animation.playState))
  );
}

export async function waitForNavigationFold(page) {
  await page.waitForFunction(navigationFolded);
  // ResizeObserver queues FooterLayout's measurement in rAF; let the final width reach that React commit too.
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

export async function verifyFooter(page, shots) {
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.waitForFunction(() => !document.querySelector("#root")?.hasAttribute("inert"));
  const footer = page.getByRole("contentinfo");
  await footer.locator('[data-footer-content="@resources"] button').waitFor();
  await footer.locator('[data-footer-content="@version"] button').waitFor();
  await footer.evaluate((bar) => {
    window.footerSyntheticResizes = 0;
    const dispatch = window.dispatchEvent;
    window.dispatchEvent = function (event) {
      if (event.type === "resize") window.footerSyntheticResizes++;
      return dispatch.call(this, event);
    };
    window.footerProofNodes = new Map(
      [...bar.querySelectorAll("[data-footer-content]")].flatMap((part) => {
        const button = part.querySelector("button");
        return button ? [[part.dataset.footerContent, button]] : [];
      }),
    );
  });
  const samples = [];
  for (const width of [1440, 900, 640, 360, 240]) {
    await page.setViewportSize({ width, height: 900 });
    if (width < 1024) {
      await waitForNavigationFold(page);
      const pageTab = page.getByRole("tab", { name: "Page", exact: true });
      if (await pageTab.isVisible()) await pageTab.click();
    }
    await page.waitForFunction(() => {
      const bar = document.querySelector("footer");
      if (!bar) return false;
      return [...bar.querySelectorAll("[data-footer-part]:not([inert])")].every((part) => {
        const box = part.getBoundingClientRect(),
          parent = bar.getBoundingClientRect();
        return box.left >= parent.left && box.right <= parent.right + 1;
      });
    });
    await page.waitForFunction(() => {
      const bar = document.querySelector("footer");
      const more = bar?.querySelector('[data-footer-content="@more"] button');
      if (!bar || !more) return false;
      const hidden = bar.querySelectorAll('[data-footer-hidden="overflow"]').length > 0;
      const style = getComputedStyle(more);
      const visible =
        style.visibility !== "hidden" && style.display !== "none" && more.getBoundingClientRect().width > 0;
      return hidden === visible;
    });
    const geometry = await footer.evaluate((bar) => {
      const bounds = bar.getBoundingClientRect();
      const style = getComputedStyle(bar);
      const parts = [...bar.querySelectorAll("[data-footer-part]")].map((part) => ({
        id: part.dataset.footerPart,
        overflow: part.dataset.footerHidden === "overflow",
        hidden: part.hasAttribute("inert"),
        left: part.getBoundingClientRect().left,
        right: part.getBoundingClientRect().right,
      }));
      return {
        left: bounds.left,
        right: bounds.right,
        paddingLeft: Number.parseFloat(style.paddingLeft),
        paddingRight: Number.parseFloat(style.paddingRight),
        text: bar.textContent,
        parts,
      };
    });
    assert.ok(!geometry.text.includes("🐻"));
    assert.ok(geometry.left >= 0 && geometry.right <= width + 1);
    assert.ok(
      Math.abs(geometry.paddingLeft - geometry.paddingRight) <= 1,
      `Footer edge padding should be symmetrical at ${width}px: ${geometry.paddingLeft}px vs ${geometry.paddingRight}px`,
    );
    const shown = geometry.parts.filter((part) => !part.hidden);
    for (let index = 1; index < shown.length; index++)
      assert.ok(shown[index - 1].right <= shown[index].left + 1, `Footer controls overlap at ${width}px`);
    assert.equal(shown[0]?.id, "@version");
    const hidden = geometry.parts.filter((part) => part.overflow);
    const more = footer.getByRole("button", { name: "More status items" });
    if (hidden.length) {
      try {
        await more.waitFor({ state: "visible", timeout: 1500 });
      } catch {
        const domState = await footer.evaluate((bar) => {
          const button = bar.querySelector('[data-footer-content="@more"] button');
          const anchor = bar.querySelector('[data-footer-part="@more"]');
          return {
            button: button && {
              rect: button.getBoundingClientRect().toJSON(),
              label: button.getAttribute("aria-label"),
              inert: button.closest("[inert]") !== null,
              ariaHidden: button.closest('[aria-hidden="true"]') !== null,
            },
            anchor: anchor && {
              ariaHidden: anchor.getAttribute("aria-hidden"),
              inert: anchor.hasAttribute("inert"),
              hidden: anchor.getAttribute("data-footer-hidden"),
            },
          };
        });
        assert.fail(
          `Overflow items are present but the accessible More control is not visible at ${width}px: ${JSON.stringify(domState)}`,
        );
      }
      assert.ok(
        await more.isVisible(),
        `Hidden footer controls need a visible More button at ${width}px: ${JSON.stringify(hidden.map((part) => part.id))}`,
      );
      await page.waitForFunction(() => {
        const bar = document.querySelector("footer");
        const button = bar?.querySelector('[data-footer-content="@more"] button');
        const anchor = bar?.querySelector('[data-footer-part="@more"]');
        if (!bar || !button || !anchor || !bar.querySelector('[data-footer-hidden="overflow"]')) return false;
        const visible = button.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
        const bounds = button.getBoundingClientRect();
        return visible && bounds.width > 0 && bounds.height > 0 && !anchor.hasAttribute("inert");
      });
      // The role query proves the control is exposed to assistive technology. Keep the actual action scoped to
      // its stable source marker so Chromium does not have to resolve the same accessible name again mid-click.
      const markedMore = footer.locator('[data-footer-content="@more"] button');
      const accessible = await markedMore.evaluate((button) => {
        const ancestors = [];
        for (let element = button; element; element = element.parentElement) {
          const style = getComputedStyle(element);
          ancestors.push({
            tag: element.tagName,
            inert: element.hasAttribute("inert"),
            ariaHidden: element.getAttribute("aria-hidden"),
            display: style.display,
            visibility: style.visibility,
            opacity: style.opacity,
            visible: element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
          });
        }
        return {
          tag: button.tagName,
          role: button.getAttribute("role") ?? (button.tagName === "BUTTON" ? "button" : null),
          name: button.getAttribute("aria-label"),
          ancestors,
        };
      });
      assert.equal(accessible.tag, "BUTTON");
      assert.equal(accessible.role, "button");
      assert.equal(accessible.name, "More status items");
      assert.ok(
        accessible.ancestors.every((ancestor) => !ancestor.inert && ancestor.ariaHidden !== "true" && ancestor.visible),
        `More control has a hidden/inert ancestor at ${width}px: ${JSON.stringify(accessible)}`,
      );
      const roleHandle = await more.elementHandle();
      assert.ok(roleHandle, `More control lost its accessible role/name at ${width}px`);
      assert.equal(
        await markedMore.evaluate((button, resolvedByRole) => button === resolvedByRole, roleHandle),
        true,
        "The accessible More button and its stable footer marker must identify the same mounted control.",
      );
      try {
        await markedMore.click({ timeout: 3000 });
      } catch (error) {
        const domState = await footer.evaluate((bar) => {
          const describe = (element) => {
            if (!(element instanceof HTMLElement)) return null;
            const style = getComputedStyle(element);
            return {
              tag: element.tagName,
              className: element.className,
              rect: element.getBoundingClientRect().toJSON(),
              visibility: style.visibility,
              display: style.display,
              opacity: style.opacity,
              role: element.getAttribute("role") ?? (element.tagName === "BUTTON" ? "button" : null),
              label: element.getAttribute("aria-label"),
              title: element.getAttribute("title"),
              inert: element.hasAttribute("inert"),
              ariaHidden: element.getAttribute("aria-hidden"),
              visible: element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
            };
          };
          const anchor = bar.querySelector('[data-footer-part="@more"]');
          const button = bar.querySelector('[data-footer-content="@more"] button');
          const ancestors = [];
          for (let element = button; element; element = element.parentElement) ancestors.push(describe(element));
          return {
            viewport: { width: innerWidth, height: innerHeight },
            footer: describe(bar),
            overflow: [...bar.querySelectorAll('[data-footer-hidden="overflow"]')].map(
              (item) => item.dataset.footerPart,
            ),
            ancestors,
            anchor: describe(anchor),
            hit: button
              ? document
                  .elementFromPoint(
                    button.getBoundingClientRect().x + button.getBoundingClientRect().width / 2,
                    button.getBoundingClientRect().y + button.getBoundingClientRect().height / 2,
                  )
                  ?.outerHTML.slice(0, 300)
              : null,
          };
        });
        assert.fail(`Could not open footer overflow at ${width}px: ${JSON.stringify(domState)}; ${error.message}`);
      }
      const popup = page.getByRole("region", { name: "More status items" });
      await popup.waitFor();
      await page.waitForFunction(() => {
        const popup = document.querySelector('section[aria-label="More status items"]');
        const expected = [...document.querySelectorAll('footer [data-footer-hidden="overflow"]')].map(
          (element) => element.dataset.footerPart,
        );
        const shown = [...(popup?.querySelectorAll("[data-footer-host]") ?? [])].map(
          (element) => element.dataset.footerHost,
        );
        return JSON.stringify(shown) === JSON.stringify(expected);
      });
      const placement = await popup.evaluate((element) => ({
        actual: [...element.querySelectorAll("[data-footer-host]")].map((host) => host.dataset.footerHost),
        expected: [...document.querySelectorAll('footer [data-footer-hidden="overflow"]')].map(
          (anchor) => anchor.dataset.footerPart,
        ),
      }));
      assert.deepEqual(placement.actual, placement.expected);
      const box = await popup.boundingBox();
      assert.ok(
        box && box.x >= 0 && box.x + box.width <= width + 1 && box.y >= 0 && box.y + box.height <= 901,
        JSON.stringify({ width, popup: box }),
      );
      for (const part of hidden) assert.equal(await page.locator(`[data-footer-host="${part.id}"]`).count(), 1);
      if (hidden.some((part) => part.id === "@resources")) {
        const resource = popup.locator('[data-footer-host="@resources"] button').first();
        await resource.click();
        const detail = page.getByRole("region", { name: "System resources", exact: true });
        await detail.waitFor();
        await page.waitForFunction(
          (element) => {
            const box = element.getBoundingClientRect();
            const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
            return (
              box.x >= 0 && box.y >= 0 && box.right <= innerWidth && box.bottom <= innerHeight && element.contains(hit)
            );
          },
          await detail.elementHandle(),
        );
        if (shots) await page.screenshot({ path: join(shots, `footer-more-${width}.png`) });
        await detail.getByRole("button", { name: "Refresh resources", exact: true }).click();
        assert.ok(await popup.isVisible(), "A nested detail action must keep its containing More list open.");
        await page.keyboard.press("Escape");
      }
      await page.keyboard.press("Escape");
      // The first Escape belongs to a focused nested detail pane; the second closes its containing More list.
      if (await popup.isVisible()) await page.keyboard.press("Escape");
      await popup.waitFor({ state: "hidden" });
      await more.focus();
    } else assert.equal(await more.count(), 0);
    samples.push({ width, visible: shown.map((part) => part.id), hidden: hidden.map((part) => part.id) });
    const changed = await page.evaluate(() =>
      [...window.footerProofNodes]
        .filter(([id, button]) => document.querySelector(`[data-footer-content="${id}"] button`) !== button)
        .map(([id, button]) => ({ id, originalConnected: button.isConnected })),
    );
    assert.deepEqual(changed, [], `Overflow must preserve mounted controls at ${width}px.`);
    assert.equal(
      await page.evaluate(() => window.footerSyntheticResizes),
      0,
      "Relocation must not simulate viewport changes.",
    );
    if (shots) await page.screenshot({ path: join(shots, `footer-${width}.png`) });
  }
  return samples;
}

// Input stays in stdin so the session fragment is never a command argument or a printed diagnostic.
if (process.argv[1]?.endsWith("/footer.mjs")) {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const { url, shots } = JSON.parse(input);
  if (shots) mkdirSync(shots, { recursive: true });
  let browser;
  try {
    browser = await chromium.launch();
  } catch {
    browser = await chromium.launch({ channel: "chrome" });
  }
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const entries = Array.from({ length: 6 }, (_, index) => ({
      id: `probe:status-${index}`,
      module: "probe",
      plugin: null,
      order: index,
      refresh: 60,
      problem: null,
      slots: { footer: true },
      item: { text: `Probe ${index}`, tone: "ok", icon: "link", detail: [{ text: "A real mounted provider row." }] },
    }));
    await page.route("**/api/system/ui/status*", async (route) => {
      const selected = new URL(route.request().url()).searchParams.get("item");
      if (selected?.startsWith("probe:")) {
        await route.fulfill({ json: entries.filter((entry) => entry.id === selected) });
        return;
      }
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({
        response,
        json: [...body, ...entries.filter((entry) => !selected || entry.id === selected)],
      });
    });
    await page.goto(url);
    await page.locator('[data-footer-part="probe:status-0"]').waitFor();
    const native = await verifyFooter(page, shots);
    await page.setViewportSize({ width: 1440, height: 900 });
    // Narrow-layout proof folds navigation by design; the existing layer probe starts with a docked player.
    await page.evaluate(() => localStorage.setItem("hz.nav.open", "true"));
    const layers = await verifyLayers(page, shots, !!shots);
    console.log(JSON.stringify({ native, layers, browser: browser.version() }));
  } finally {
    await browser.close();
  }
}
