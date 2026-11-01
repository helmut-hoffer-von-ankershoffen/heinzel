// Exercise an arbitrary provider's navigation and the actual authenticated private JSON operation it invokes.
export async function verifyNavigation(page, url, shots) {
  const base = new URL(url).origin;
  const token = new URLSearchParams(new URL(url).hash.slice(1)).get("token");
  const headers = { Authorization: `Bearer ${token}` };
  const create = await page.request.post(`${base}/api/heinzel/new-chat`, {
    headers,
    data: { request_id: crypto.randomUUID(), name: "Harbor helper" },
  });
  if (!create.ok()) throw new Error(`Private fixture creation failed: ${create.status()}`);
  const session = await create.json();
  const body = {
    session_id: session.session_id,
    request_id: crypto.randomUUID(),
    expected_revision: session.profile_revision,
    name: "Harbor conditions",
    appearance: { age: "grown", build: "regular", hat: "blue", accessories: ["scarf"] },
  };
  const entry = {
    id: "harbor:conditions",
    module: "harbor",
    plugin: "heinzel-harbor",
    title: "Local conditions",
    icon: "cloud-sun",
    declared: true,
    order: 1,
    settings: { group: "preferences", sections: ["harbor"] },
    sidebar: {
      kind: "operation",
      operation: "heinzel:profile",
      arguments: body,
      call: {
        method: "POST",
        path: "/heinzel/profile",
        query: {},
        body,
        command: "hz heinzel profile",
        summary: "Save the helper profile.",
        read_only: false,
      },
    },
  };
  await page.route("**/api/system/ui/navigation", (route) => route.fulfill({ json: [entry] }));
  await page.route("**/api/system/config/show", async (route) => {
    const response = await route.fetch();
    const report = await response.json();
    await route.fulfill({
      response,
      json: {
        ...report,
        settings: [
          ...report.settings,
          {
            section: "harbor",
            section_title: "Conditions service",
            key: "place",
            value: "Lisbon",
            default: "Lisbon",
            description: "The location to display.",
            source: "default",
            env: "HZ_HARBOR__PLACE",
            json_schema: { type: "string", title: "Weather location" },
          },
        ],
      },
    });
  });
  await page.goto(`${base}/doctor?settings=harbor`);
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"));
  const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
  await dialog.getByLabel("Weather location", { exact: true }).waitFor();
  const entryButton = dialog
    .getByRole("navigation", { name: "Settings" })
    .getByRole("button", { name: "Local conditions" });
  if (!(await entryButton.locator("svg.lucide-cloud-sun").count()))
    throw new Error("Provider semantic icon was replaced");
  if (await dialog.getByText("Private", { exact: true }).count()) throw new Error("Unknown provenance became private");
  await page.screenshot({ path: `${shots}/provider-settings.png` });
  const viewport = page.viewportSize();
  await page.setViewportSize({ width: 360, height: 800 });
  const picker = dialog.getByRole("combobox", { name: "Settings page", exact: true });
  await picker.waitFor({ state: "visible" });
  await picker.selectOption("appearance");
  await picker.selectOption(entry.id);
  await dialog.getByLabel("Weather location", { exact: true }).waitFor();
  await picker.focus();
  await page.keyboard.press("Tab");
  const search = dialog.getByRole("searchbox", { name: "Filter settings" });
  if (!(await search.evaluate((element) => element === document.activeElement)))
    throw new Error("Narrow Settings search is not reachable after page selection");
  await search.fill("location");
  await dialog.getByRole("heading", { name: "Matching settings" }).waitFor();
  if (!(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)))
    throw new Error("Narrow provider Settings overflows horizontally");
  await page.screenshot({ path: `${shots}/provider-settings-narrow.png` });
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  if (viewport) await page.setViewportSize(viewport);
  let sent;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/stream/heinzel/profile") sent = request; // a confirmed write runs on the operation stream
  });
  await page
    .getByRole("navigation", { name: "Main", exact: true })
    .getByRole("button", { name: "Local conditions" })
    .click();
  const confirmation = page.getByRole("alertdialog", { name: "Save the helper profile." });
  await confirmation.waitFor();
  if (sent) throw new Error("A private mutation ran before confirmation");
  const answer = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/stream/heinzel/profile");
  await confirmation.getByRole("button", { name: "Run it" }).click();
  const changed = await answer;
  if (!changed.ok() || !sent || new URL(sent.url()).search || sent.postDataJSON().appearance.hat !== "blue")
    throw new Error("The generic action did not use the private JSON wire");
  const check = await page.request.get(`${base}/api/heinzel/session`, {
    headers,
    params: { session_id: session.session_id },
  });
  const saved = await check.json();
  if (
    !check.ok() ||
    saved.name !== body.name ||
    saved.appearance.hat !== "blue" ||
    saved.appearance.accessories[0] !== "scarf"
  )
    throw new Error("The private action did not persist the same result as the direct API");
  await page.unroute("**/api/system/ui/navigation");
  await page.unroute("**/api/system/config/show");
  const removed = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/system/ui/navigation");
  await page.reload();
  await removed;
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"));
  await page.getByRole("navigation", { name: "Main", exact: true }).waitFor();
  if (await page.getByRole("button", { name: "Local conditions", exact: true }).count())
    throw new Error("Removed provider navigation survived a runtime refresh");
  return { providerSettings: true, compactSettings: true, privateJsonParity: true, removedProviderAbsent: true };
}
