// Real Electron main, preload and renderer, two controlled answering services and an isolated CLI attachment.
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { createServer as createTLS } from "node:https";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron } from "../../web/node_modules/playwright-core/index.mjs";

const root = await mkdtemp(join(tmpdir(), "workspace-picker-"));
const token = randomBytes(32).toString("base64url");
const paired = randomBytes(32).toString("base64url");
const key = join(root, "fixture.key"), cert = join(root, "fixture.pem");
await writeFile(join(root, "cert.cnf"), "[req]\ndistinguished_name=subject\nx509_extensions=ext\nprompt=no\n[subject]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n");
execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
  "-config", join(root, "cert.cnf"), "-keyout", key, "-out", cert], { stdio: "ignore" });
let outage = false, expired = false, pairedReads = 0, installs = "0.9.58", probes = 0, writes = 0;
let metadataUnavailable = false, pairedFacts = 0;
const routes = (remote) => (req, res) => {
  if (req.method !== "GET") writes++;
  if (req.url.startsWith("/api/")) {
    const usesPair = remote && req.headers.authorization === "Bearer " + paired;
    if (usesPair) pairedReads++;
    if (!usesPair && (req.headers.authorization !== "Bearer " + token || (remote && expired))) { res.writeHead(401).end(); return; }
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/api/lifecycle") {
      if (remote) probes++;
      if (remote && outage) { res.writeHead(503).end(); return; }
      res.end(JSON.stringify({ version: "0.9.58", instance: remote ? "other-instance" : "local-instance" })); return;
    }
    if (usesPair) pairedFacts++;
    if (metadataUnavailable) { res.writeHead(503).end(); return; }
    res.end(JSON.stringify({ web: { version: "0.9.58", backend_ip: usesPair ? "2001:db8::8" : "127.0.0.1", running_since: "2026-01-01T00:00:00Z" },
      installed: { version: installs }, observed_at: "2026-01-01T00:02:00Z" })); return;
  }
  res.setHeader("Content-Type", "text/html");
  res.end(`<!doctype html><meta charset="utf-8"><main data-testid="app-shell"><button id="profile">Profile</button>
    <textarea id="draft"></textarea></main><script>
    const token = new URLSearchParams(location.hash.slice(1)).get("token");
    if(token) sessionStorage.setItem("hz.token",token); history.replaceState(null,"","/chat/retained");
    profile.onclick=()=>{
      const menu = document.createElement("div");
      menu.setAttribute("role","menu");
      menu.innerHTML='<a role="menuitem" href="?settings=">Settings</a><a role="menuitem" href="/profile">Profile</a><button role="menuitem" aria-haspopup="dialog">About</button>';
      document.body.append(menu);
    };
    window.addEventListener("keydown", event => {
      if(event.key==="Escape") { document.querySelector('[role="menu"]')?.remove(); profile.focus(); }
    });</script>`);
};
const local = createServer(routes(false)), remote = createTLS({ key: await readFile(key), cert: await readFile(cert) }, routes(true));
const listen = (server) => new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
await Promise.all([listen(local), listen(remote)]);
const localURL = "http://127.0.0.1:" + local.address().port, remoteURL = "https://127.0.0.1:" + remote.address().port;
const pickerURL = new URL("../src/workspace-picker.html", import.meta.url).href;
let app;
try {
  for (const dir of ["home", "bin", "config", "cache", "data", "state", "tmp", "logs", "electron-profile", "electron-session", "electron-logs"])
    await mkdir(join(root, dir), { mode: 0o700 });
  await writeFile(join(root, "bin/hz"), `#!${process.execPath}\nif(JSON.stringify(process.argv.slice(2))!==JSON.stringify(["ui","attach","--bridge-token-stdin"]))process.exit(64);\nprocess.stdin.resume();process.stdin.on("end",()=>console.log(process.env.HZ_TEST_URL));\n`, { mode: 0o700 });
  const env = { HOME: join(root, "home"), PATH: join(root, "bin") + ":/usr/bin:/bin", TMPDIR: join(root, "tmp"),
    XDG_CONFIG_HOME: join(root, "config"), XDG_CACHE_HOME: join(root, "cache"), XDG_DATA_HOME: join(root, "data"),
    HZ_CONFIG_HOME: join(root, "config"), HZ_DATA_DIR: join(root, "data"), HZ_STATE_DIR: join(root, "state"),
    HZ_CACHE_DIR: join(root, "cache"), HZ_LOG_DIR: join(root, "logs"), HZ_NATIVE_TEST_ROOT: root,
    HZ_TEST_URL: localURL + "/#token=" + token, HZ_TEST_CERT: cert, NODE_EXTRA_CA_CERTS: cert };
  const project = await readFile(new URL("../../pyproject.toml", import.meta.url), "utf8");
  const version = /^version\s*=\s*"([^"]+)"/m.exec(project)[1];
  await writeFile(join(root, "package.json"), JSON.stringify({ name: "heinzel-workspace-fixture", type: "module", version, main: "bootstrap.mjs" }));
  await writeFile(join(root, "bootstrap.mjs"), "import " + JSON.stringify(new URL("./workspace-picker-bootstrap.mjs", import.meta.url).href));
  app = await electron.launch({ executablePath: process.env.HZ_TEST_ELECTRON, env, cwd: root,
    args: [root], timeout: 30000 });
  const pageAt = async (prefix, previous) => {
    const until = Date.now() + 22000;
    while (Date.now() < until) {
      const match = app.windows().reverse().find((page) => page !== previous && !page.isClosed() && page.url().startsWith(prefix));
      if (match) return match;
      await new Promise((resolve) => setTimeout(resolve, 80));
    }
    throw new Error("Missing " + (prefix === pickerURL ? "chooser" : "product window"));
  };
  const menu = () => app.evaluate(({ Menu }) => {
    Menu.getApplicationMenu().items[0].submenu.items.find((item) => item.label === "Workspaces…").click();
  });
  async function waitForBridge(page, predicate, description) {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (await page.evaluate(predicate).catch(() => false)) return;
      await new Promise((resolve) => setTimeout(resolve, 80));
    }
    throw new Error("Timed out waiting for " + description);
  }
  console.log("Local attach");
  const product = await pageAt(localURL);
  await waitForBridge(product, async () => await window.heinzelDesktop.workspaces.currentName() === "This Mac", "local selection");
  await product.locator("#profile").click();
  const localItem = product.getByRole("menuitem", { name: /Workspaces….*This Mac/ });
  await localItem.waitFor();
  await product.keyboard.press("Escape");
  await product.locator("#profile").click();
  await localItem.press("Enter");
  let picker = await pageAt(pickerURL);
  await picker.getByRole("button", { name: "Information", exact: true }).click();
  await picker.getByRole("heading", { name: "Workspace", exact: true }).waitFor();
  assert.equal(await picker.locator("#version").textContent(), "v" + version);
  await picker.locator("#workspace").getByText("120 s").waitFor();
  assert.equal(await picker.getByText("Installed version", { exact: true }).count(), 0);
  await picker.getByRole("button", { name: "Back", exact: true }).click();
  await picker.getByRole("button", { name: "Disconnect from This Mac" }).click();
  picker = await pageAt(pickerURL, picker);
  const geometry = [];
  const cdp = await picker.context().newCDPSession(picker);
  await cdp.send("Accessibility.enable");
  assert.equal(await picker.locator("header p").innerText(),
    "Durable agents accelerating product teams,\nlike the little elves from Cologne.");
  for (const { width, fontSize } of [{ width: 720, fontSize: "100%" }, { width: 320, fontSize: "125%" }]) {
    const footerPositions = [];
    for (const height of [600, 850, 1100]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
      const samples = await picker.evaluate(async (fontSize) => {
        const original = await window.workspacePicker.state();
        const bounds = (selector) => document.querySelector(selector).getBoundingClientRect();
        const centerX = (rect) => (rect.left + rect.right) / 2;
        document.documentElement.style.fontSize = fontSize;
        const samples = [4, 24].map((count) => {
          const recent = Array.from({ length: count - 1 }, (_, index) => ({ origin: "https://host" + index + ".example",
            name: "Studio", version: "0.9.58", signedIn: false }));
          render({ ...original, recent });
          const group = bounds(".group"), surface = bounds("#chooser"), footer = bounds("footer"), list = document.querySelector("#rows");
          const headingTop = bounds("#heading").top;
          list.scrollTop = list.scrollHeight;
          return { count, footerBottom: footer.bottom, footerCenter: centerX(footer), groupCenter: centerX(group),
            groupWidth: group.width, verticalOffset: (group.top + group.bottom - surface.top - surface.bottom) / 2,
            headingShift: bounds("#heading").top - headingTop, listScroll: list.scrollTop, pageWidth: document.documentElement.scrollWidth, pageHeight: document.documentElement.scrollHeight };
        });
        render(original); return samples;
      }, fontSize);
      for (const sample of samples) {
        assert.equal(sample.footerCenter, width / 2); assert.equal(sample.groupCenter, width / 2);
        assert.ok(sample.groupWidth <= 440 && Math.abs(sample.verticalOffset) < 1);
        assert.ok(height - sample.footerBottom >= 0 && height - sample.footerBottom < 20);
        assert.equal(sample.headingShift, 0); assert.equal(sample.pageHeight, height); assert.equal(sample.pageWidth, width);
      }
      assert.equal(samples[0].footerBottom, samples[1].footerBottom);
      assert.ok(samples[1].listScroll > 0);
      footerPositions.push(samples[0].footerBottom);
      geometry.push({ width, height, fontSize, samples });
    }
    assert.equal(footerPositions[1] - footerPositions[0], 250);
    assert.equal(footerPositions[2] - footerPositions[1], 250);
    const { nodes } = await cdp.send("Accessibility.getFullAXTree");
    const titles = nodes.filter(node => node.role?.value === "heading" &&
      node.properties?.some(property => property.name === "level" && property.value.value === 1));
    assert.deepEqual(titles.map(node => node.name.value), ["Heinzelmännchen"]);
  }
  await picker.evaluate(() => { document.documentElement.style.fontSize = ""; });
  await cdp.send("Emulation.clearDeviceMetricsOverride");
  console.log("Chooser geometry", JSON.stringify(geometry));
  await picker.getByRole("button", { name: "Add workspace" }).click();
  await picker.getByLabel("Sign-in URL", { exact: true }).fill(remoteURL + "/#token=" + token);
  await picker.getByLabel("Sign-in URL", { exact: true }).press("Enter");
  console.log("Remote selection");
  const other = await pageAt(remoteURL);
  await other.locator("#draft").fill("unsent fixture draft");
  await other.evaluate(() => {
    window.restoreEvents = [];
    window.addEventListener("hz:workspace-restored", (event) => window.restoreEvents.push(event.detail === undefined));
  });
  await waitForBridge(other, async () => await window.heinzelDesktop.workspaces.currentName() === "127.0.0.1", "remote selection");
  installs = "0.9.59"; console.log("Remote Information");
  await other.locator("#profile").click();
  const remoteItem = other.getByRole("menuitem", { name: /Workspaces….*127\.0\.0\.1/ });
  await remoteItem.waitFor();
  await remoteItem.focus();
  await other.evaluate(() => {
    const item = document.createElement("button");
    item.setAttribute("role", "menuitem"); item.setAttribute("aria-haspopup", "dialog");
    item.setAttribute("aria-label", "Workspaces… 127.0.0.1"); item.textContent = "Workspaces…";
    document.querySelector('[role="menu"]').append(item);
  });
  await other.locator("[data-hz-legacy-workspaces]").waitFor({ state: "detached" });
  assert.equal(await remoteItem.count(), 1);
  assert.ok(await remoteItem.evaluate(item => document.activeElement === item));
  await other.keyboard.press("Escape");
  await other.locator("#profile").click();
  await remoteItem.click(); picker = await pageAt(pickerURL, picker);
  await picker.getByRole("button", { name: "Information", exact: true }).click();
  await picker.getByText("Installed version", { exact: true }).waitFor();
  // Keep the modal, route and focus in place while the owning document changes authority.
  metadataUnavailable = true;
  await other.evaluate(value => sessionStorage.setItem("hz.token", value), paired);
  const known = picker.locator("#workspace");
  await known.getByText("120 s", { exact: true }).waitFor({ state: "detached", timeout: 5000 });
  assert.equal(await known.getByText("Installed version", { exact: true }).count(), 0);
  assert.equal(await picker.locator("#application dd").last().textContent(), "Unknown");
  assert.equal(await known.locator("dt").filter({hasText: "IP address"}).locator("+ dd").textContent(), "Unknown");
  metadataUnavailable = false;
  await other.evaluate(value => sessionStorage.setItem("hz.token", value), token);
  await known.getByText("120 s", { exact: true }).waitFor({timeout: 5000});
  expired = true;
  await other.evaluate(value => localStorage.setItem("hz.device", value), paired);
  await known.getByText("2001:db8::8", { exact: true }).waitFor({timeout: 5000});
  expired = false;
  await other.evaluate(() => localStorage.removeItem("hz.device"));
  await known.getByText("127.0.0.1", { exact: true }).waitFor({timeout: 5000});
  console.log("Open Information cleared earlier facts and refreshed under same-document session and device credential changes");
  await picker.getByRole("button", { name: "Back", exact: true }).click();
  await Promise.all([picker.waitForEvent("close"), picker.keyboard.down("Escape")]);
  outage = true; console.log("Remote loss");
  picker = await pageAt(pickerURL, picker);
  await picker.getByRole("button", { name: "Add workspace" }).click();
  await picker.getByLabel("Sign-in URL", { exact: true }).fill("still editing");
  await menu();
  assert.equal(await picker.getByLabel("Sign-in URL", { exact: true }).inputValue(), "still editing");
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();
  if (process.env.HZ_TEST_CHOOSER_IMAGE) await picker.screenshot({ path: process.env.HZ_TEST_CHOOSER_IMAGE });
  await picker.getByRole("button", { name: /Pause reconnecting/ }).click();
  await picker.getByText(/Paused ·/).waitFor();
  const pausedCount = probes; outage = false; expired = true;
  await other.evaluate((device) => {
    localStorage.setItem("hz.device", device);
  }, paired);
  assert.equal(await other.evaluate(() => sessionStorage.getItem("hz.token")) === token, true);
  await picker.evaluate(() => window.dispatchEvent(new Event("online")));
  await picker.getByRole("button", { name: /Resume reconnecting/ }).waitFor();
  assert.equal(probes, pausedCount);
  assert.deepEqual(await other.evaluate(() => window.restoreEvents), []);
  await picker.getByRole("button", { name: /Resume reconnecting/ }).click();
  await waitForBridge(picker, async () => !(await window.workspacePicker.state()).recovery, "authenticated recovery");
  await other.waitForFunction(() => location.pathname === "/chat/retained");
  await other.waitForFunction(() => window.restoreEvents.length);
  assert.deepEqual(await other.evaluate(() => window.restoreEvents), [true]);
  assert.equal(await other.evaluate(() => sessionStorage.getItem("hz.token")) === token, true);
  assert.equal(await other.locator("#draft").inputValue(), "unsent fixture draft");
  assert.equal(writes, 0); assert.ok(pairedReads > 0);
  installs = null;
  await menu(); picker = await pageAt(pickerURL, picker);
  await picker.getByRole("button", { name: "Information", exact: true }).click();
  await picker.getByText("2001:db8::8", { exact: true }).waitFor();
  assert.ok(pairedFacts > 0);
  assert.equal(await picker.getByText("Installed version", { exact: true }).count(), 0);
  metadataUnavailable = true;
  await menu();
  await picker.waitForFunction(() => !document.getElementById("workspace").textContent.includes("2001:db8::8"));
  assert.equal(await picker.locator("#workspace").getByText("120 s").count(), 0);
  await picker.getByRole("button", { name: "Back", exact: true }).click();
  console.log("Paired Information refreshed the backend address; unavailable metadata cleared the address, uptime and installed version");
  await picker.getByRole("button", { name: /This Mac.*Local backend/ }).click();
  const returned = await pageAt(localURL, product);
  await waitForBridge(returned, async () => (await window.heinzelDesktop.lifecycle()).instance === "local-instance", "restored local instance");
  assert.equal(await returned.evaluate(() => sessionStorage.getItem("hz.token")) === token, true);
  await menu(); picker = await pageAt(pickerURL, picker);
  await picker.getByRole("button", { name: /127.0.0.1.*Manual/ }).click();
  await picker.getByRole("dialog", { name: "Sign in" }).waitFor();
  assert.equal(await picker.getByLabel("Sign-in URL", { exact: true }).evaluate((input) => document.activeElement === input), true);
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(await returned.evaluate(async () => (await window.heinzelDesktop.lifecycle()).instance), "local-instance");
  console.log(JSON.stringify({ electron: await app.evaluate(() => process.versions.electron),
    serverPid: process.pid, localInstance: "local-instance", sameSession: true, draftRetained: true,
    remoteAuthenticatedReads: probes, pairedReads, recoveredWithStaleLaunchStillStored: true,
    expiredSavedRowOpensSignIn: true, addSurvivedMenu: true, writes, syntheticBackends: true }));
} finally {
  if (app) await app.close();
  local.closeAllConnections(); remote.closeAllConnections(); local.close(); remote.close();
}
