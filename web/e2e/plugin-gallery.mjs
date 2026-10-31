// Synthetic metadata exercises the real Plugins UI without fetching a public repository or provider.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const axeSource = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const revision = "a".repeat(40);
const imageBase = `https://raw.githubusercontent.com/fictional-lab/harbor/${revision}/`;
const image = readFileSync(new URL("../../docs/assets/web/home.png", import.meta.url));
const specification = `heinzel-harbor @ git+https://github.com/fictional-lab/harbor@${revision}`;

export async function contributeGallery(page) {
  const requests = { metadata: 0, images: 0, external: [] };
  await page.route("**/api/system/plugin/search**", (route) =>
    route.fulfill({
      json: {
        plugins: [
          {
            name: "heinzel-harbor",
            short_name: "harbor",
            description: "Fictional gallery fixture.",
            install_spec: specification,
            repository_url: "https://github.com/fictional-lab/harbor",
            source: "github",
            community: true,
            private_repository: false,
            stars: 0,
            installed: false,
          },
        ],
        warnings: [],
      },
    }),
  );
  await page.route("**/api/system/plugin/showcase?*", (route) => {
    requests.metadata++;
    assert.equal(new URL(route.request().url()).searchParams.get("spec"), specification);
    return route.fulfill({
      json: {
        title: "Harbor",
        revision,
        notice: "Fictional owner fixture.",
        screenshots: [
          { surface: "ui", caption: "Harbor UI preview", url: `${imageBase}ui.png` },
          { surface: "cli", caption: "Harbor CLI preview", url: `${imageBase}cli.png` },
        ],
        soundscapes: [{ title: "Harbor tide", url: "https://www.youtube.com/watch?v=abcdefghijk" }],
      },
    });
  });
  await page.route(`${imageBase}**`, (route) => {
    requests.images++;
    return route.fulfill({ contentType: "image/png", body: image });
  });
  page.on("request", (request) => {
    if (/youtube/.test(request.url())) requests.external.push(request.url());
  });
  return requests;
}

export async function exercisePluginGallery(page, requests, shots, name) {
  const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
  await dialog.getByRole("tab", { name: "Discover", exact: true }).click();
  const button = dialog.getByRole("button", { name: "Screenshots for Harbor", exact: true });
  await button.waitFor();
  assert.equal(requests.metadata, 0, "Gallery metadata must remain lazy");
  assert.equal(requests.images, 0, "No external image request before opening its gallery");
  await button.click();
  const gallery = dialog.getByRole("region", { name: "Screenshot gallery: Harbor", exact: true });
  await gallery.getByRole("img", { name: "Harbor UI preview", exact: true }).evaluate((element) => element.decode());
  const cli = gallery.getByRole("button", { name: "CLI: Harbor CLI preview", exact: true });
  await cli.click();
  const selected = gallery.getByRole("img", { name: "Harbor CLI preview", exact: true });
  await selected.evaluate((element) => element.decode());
  assert.equal(await cli.getAttribute("aria-pressed"), "true");
  assert.equal(
    await gallery.getByRole("link", { name: "Open full size: Harbor CLI preview" }).getAttribute("href"),
    `${imageBase}cli.png`,
  );
  assert.equal(
    await gallery.getByRole("link", { name: /Harbor tide/ }).getAttribute("href"),
    "https://www.youtube.com/watch?v=abcdefghijk",
  );
  assert.equal(await gallery.locator("iframe").count(), 0, "A soundscape link must not load a player");
  assert.deepEqual(requests.external, [], "YouTube must not receive a request before a deliberate visit");
  const content = dialog.locator("[data-settings-content]");
  const sizing = await content.evaluate((element) => ({ width: element.clientWidth, scroll: element.scrollWidth }));
  assert.ok(sizing.scroll <= sizing.width + 1, `Gallery overflows its settings pane: ${JSON.stringify(sizing)}`);
  const target = await cli.boundingBox();
  assert.ok(target && target.width >= 24 && target.height >= 24, "Screenshot chooser needs usable touch targets");
  await page.evaluate(axeSource);
  const violations = await gallery.evaluate(
    async (element) =>
      (
        await window.axe.run(element, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
        })
      ).violations,
  );
  assert.deepEqual(
    violations.map((item) => `${item.id}: ${item.help}`),
    [],
  );
  await page.screenshot({ path: join(shots, `${name}-plugin-gallery.png`) });
  await button.click();
  await gallery.waitFor({ state: "hidden" });
  await dialog.getByRole("tab", { name: "Installed", exact: true }).click();
}
