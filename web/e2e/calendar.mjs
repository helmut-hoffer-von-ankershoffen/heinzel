import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import axe from "axe-core";
import { chromium, webkit } from "playwright-core";

const [url, shots] = process.argv.slice(2);
await mkdir(shots, { recursive: true });
for (const [name, engine] of [
  ["chromium", chromium],
  ["webkit", webkit],
]) {
  const browser = await engine.launch({ headless: true });
  try {
    for (const [size, viewport] of [
      ["phone", { width: 375, height: 812 }],
      ["desktop", { width: 1280, height: 800 }],
    ]) {
      const context = await browser.newContext({ viewport, serviceWorkers: "block" });
      const page = await context.newPage();
      let served = 0;
      const requests = [];
      page.on("request", (request) => {
        if (request.url().includes("/api/org/calendar")) requests.push(request.url());
      });
      let installs = 0;
      await page.route("**/api/org/calendar-reminders-status", (route) =>
        route.fulfill({ json: { enabled: true, service: "not installed" } }),
      );
      await page.route("**/api/stream/org/calendar-reminders-install*", (route) => {
        installs += 1;
        assert.equal(new URL(route.request().url()).searchParams.get("confirm"), "true");
        return route.fulfill({
          contentType: "text/event-stream",
          body: 'event: result\ndata: {"data":"Calendar reminder service installed."}\n\n',
        });
      });
      const now = Date.now();
      const current = {
        id: "a",
        title: "Standup with an exceptionally long meeting title that must not hide the ending time",
        start: new Date(now - 60000).toISOString(),
        end: new Date(now + 600000).toISOString(),
        all_day: false,
        conference_url: "https://meet.example/test",
        organizer: { name: "Ada Lovelace", email: "ada@example.com" },
        attendees: [{ name: "Ada Lovelace" }, { email: "bob@example.com" }],
      };
      const next = {
        id: "b",
        title: "Review with a very long title that must not hide the starting time",
        start: new Date(now + 1200000).toISOString(),
        end: new Date(now + 1800000).toISOString(),
        all_day: false,
      };
      const past = (id, days) => ({
        id,
        title: `Earlier meeting ${id}`,
        start: new Date(now - days * 86400000).toISOString(),
        end: new Date(now - days * 86400000 + 1800000).toISOString(),
        all_day: false,
      });
      await page.route("**/api/org/agenda", (route) =>
        route.fulfill({ json: { current, next, problem: null, timezone: "UTC" } }),
      );
      await page.route(
        (candidate) => candidate.pathname === "/api/org/calendar",
        (route) => {
          served += 1;
          return route.fulfill({
            json: {
              since: new Date(now - 3 * 86400000).toISOString(),
              until: new Date(now + 4 * 86400000).toISOString(),
              timezone: "UTC",
              events: [past("two", 2), past("one", 1), current, next],
              problem: null,
              fix: null,
            },
          });
        },
      );
      await page.goto(url);
      await page.locator("#splash").waitFor({ state: "hidden" });
      const footer = page.locator("footer");
      const button = footer.getByRole("button", {
        name: /^Calendar: In Standup with an exceptionally long .*min left.*, off$/,
      });
      await button.waitFor({ state: "visible" });
      const glyph = button.locator('svg[data-glyph="meeting"]');
      await glyph.waitFor({ state: "visible" });
      const box = await glyph.boundingBox();
      assert.ok(box && Math.abs(box.width - 24) < 0.5 && Math.abs(box.height - 24) < 0.5, "the glyph keeps 24px");
      assert.match(await glyph.locator("[data-label]").textContent(), /^\d+$/, "minutes left sit inside the ring");
      assert.doesNotMatch(await button.textContent(), /Standup|Review/, "meeting names stay inside the pane");
      await page.screenshot({ path: join(shots, `${name}-${size}-footer.png`) });
      await button.click({ timeout: 10000 });
      const summary = page.getByRole("region", { name: "Calendar summary", exact: true });
      await summary.waitFor({ state: "visible" });
      assert.match(await summary.textContent(), /In Standup with an exceptionally long meeting title/);
      assert.match(await summary.textContent(), /Next: Review with a very long title/);
      const times = summary.locator("time");
      assert.equal(await times.count(), 4);
      const expected = [current.start, current.end, next.start, next.end].map((value) =>
        new Intl.DateTimeFormat("en-GB", {
          timeZone: "UTC",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
        }).format(new Date(value)),
      );
      for (const [index, time] of (await times.all()).entries()) {
        assert.ok((await time.textContent())?.includes(expected[index]), "wrong meeting time");
      }
      for (const time of await times.all()) {
        assert.ok(
          await time.evaluate((element) => {
            const own = element.getBoundingClientRect();
            let parent = element.parentElement;
            while (parent && parent !== document.body) {
              const box = parent.getBoundingClientRect();
              if (
                own.left < box.left - 0.5 ||
                own.right > box.right + 0.5 ||
                own.top < box.top - 0.5 ||
                own.bottom > box.bottom + 0.5
              )
                return false;
              parent = parent.parentElement;
            }
            return element.scrollWidth <= element.clientWidth && own.width > 0;
          }),
          "meeting time was clipped",
        );
      }
      await summary.getByRole("button", { name: "Open calendar", exact: true }).click({ timeout: 10000 });
      const panel = page.getByRole("region", { name: "Calendar", exact: true });
      await page
        .getByRole("region", { name: "Calendar summary", exact: true })
        .waitFor({ state: "detached", timeout: 10000 });
      try {
        await panel
          .getByText("Review with a very long title that must not hide the starting time", { exact: true })
          .first()
          .waitFor({ timeout: 10000 });
      } catch (error) {
        await page.screenshot({ path: join(shots, `${name}-${size}-panel-failed.png`) });
        throw new Error(
          `${name}/${size}: served=${served}, requests=${requests.join(",")}; ${await page.getByRole("region", { name: "Calendar", exact: true }).textContent()}; ${error}`,
        );
      }
      assert.ok(
        await panel.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
        "calendar panel overflows",
      );
      // Day-grouped agenda: two past days above Today, Today in view on open, headers sticky, and a band without controls.
      const headings = panel.getByRole("heading", { level: 3 });
      assert.ok((await headings.count()) >= 3, `${name}/${size}: expected two past days and Today`);
      const todayHeader = panel.getByRole("heading", { name: /^Today/ });
      const scroller = panel.locator("div.overflow-y-auto");
      const inView = (header) =>
        header.evaluate((element) => {
          const own = element.getBoundingClientRect();
          const box = element.closest("div.overflow-y-auto").getBoundingClientRect();
          return own.top >= box.top - 1 && own.bottom <= box.bottom + 1;
        });
      for (let attempt = 0; attempt < 50 && !(await inView(todayHeader)); attempt++) await page.waitForTimeout(100);
      assert.ok(await inView(todayHeader), `${name}/${size}: the Today header is not in view after opening`);
      for (const heading of await headings.all()) {
        assert.equal(await heading.evaluate((element) => getComputedStyle(element).position), "sticky");
      }
      await scroller.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      assert.ok(await inView(todayHeader), `${name}/${size}: the Today header left the view while scrolling`);
      await scroller.evaluate((element) => {
        element.scrollTop = 0;
      });
      const band = panel.getByText("Reminders need a background worker on this computer.", { exact: true });
      await band.waitFor();
      assert.equal(await band.locator("a, button").count(), 0, "the reminder band holds a control");
      await page.evaluate(axe.source);
      const violations = await panel.evaluate(async (element) =>
        (
          await window.axe.run(element, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
          })
        ).violations.map((v) => v.id),
      );
      assert.deepEqual(violations, []);
      await page.screenshot({ path: join(shots, `${name}-${size}.png`) });
      await panel.getByRole("button", { name: /^Standup with an exceptionally long/ }).click();
      const detail = panel.getByRole("article");
      await detail.getByRole("link", { name: "Join meeting", exact: true }).waitFor({ timeout: 10000 });
      assert.match(await detail.textContent(), /Organizer\s*Ada Lovelace/);
      assert.match(await detail.textContent(), /bob@example\.com/);
      await page.screenshot({ path: join(shots, `${name}-${size}-detail.png`) });
      await detail.getByRole("button", { name: "Back to the week", exact: true }).click();
      await detail.waitFor({ state: "detached" });
      assert.ok(!(await panel.textContent()).includes("hz org"), "setup exposed a terminal command");
      const lingering = await page.getByRole("region", { name: "Calendar summary", exact: true }).count();
      assert.equal(
        lingering,
        0,
        `${name}/${size}: the footer pane is still open; focus=${await page.evaluate(() => document.activeElement?.outerHTML.slice(0, 160))}`,
      );
      await panel.getByRole("button", { name: "Set up reminder worker" }).click();
      const confirmation = page.locator("[data-operation]");
      await confirmation.waitFor({ state: "visible" });
      assert.match(await confirmation.textContent(), /at every login/);
      assert.match(await confirmation.textContent(), /separate consent/);
      assert.equal(installs, 0);
      await page.screenshot({ path: join(shots, `${name}-${size}-setup.png`) });
      await confirmation.getByRole("button", { name: "Cancel" }).click();
      assert.equal(installs, 0);
      await panel.getByRole("button", { name: "Set up reminder worker" }).click();
      await confirmation.getByRole("button", { name: "Run it" }).click();
      await confirmation.getByText("Done.", { exact: true }).waitFor();
      assert.equal(installs, 1);
      await context.close();
    }
  } finally {
    await browser.close();
  }
}
console.log(JSON.stringify({ calendar: "chromium and webkit, phone and desktop" }));
