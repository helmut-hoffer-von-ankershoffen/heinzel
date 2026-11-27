import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createCipheriv, createHash, pbkdf2Sync } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { appMenuTemplate } from "../src/app-menu.js";
import { chromeProfiles, classifySite, decryptCookies, decryptValue, deriveKey, importLogLine, readCookieRows,
  registrableDomain, siteList, toCookie } from "../src/chrome-cookies.js";
import { createCookieImport, isDialogFrame } from "../src/cookie-import.js";

// A made-up Keychain password: the tests never touch a real Keychain or a real Chrome profile.
const PASSWORD = "fixture-safe-storage-password";
const NOW = Date.UTC(2026, 10, 22);
// Chrome's time: microseconds since 1601-01-01.
const chromeTime = (ms) => BigInt(ms) * 1000n + 11644473600n * 1000000n;
const sqliteAvailable = (() => { try { execFileSync("sqlite3", ["-version"]); return true; } catch { return false; } })();

function encrypt(value, host, version, password = PASSWORD) {
  const key = pbkdf2Sync(password, "saltysalt", 1003, 16, "sha1");
  const plain = Buffer.from(value, "utf8");
  const body = version >= 24 ? Buffer.concat([createHash("sha256").update(host).digest(), plain]) : plain;
  const cipher = createCipheriv("aes-128-cbc", key, Buffer.alloc(16, 0x20));
  return Buffer.concat([Buffer.from("v10"), cipher.update(body), cipher.final()]);
}

const COOKIES = [
  // host, name, value, path, expires (ms or 0), secure, httponly, samesite
  [".example.com", "sid", "alpha", "/", NOW + 86400000, 1, 1, 1],
  ["shop.example.com", "cart", "beta", "/cart", 0, 0, 0, -1],
  ["www.example.org", "__Host-token", "gamma", "/", NOW + 86400000, 1, 1, 2],
  [".paypal.com", "pp", "delta", "/", NOW + 86400000, 1, 1, 0],
  ["mail.google.com", "GMAIL", "epsilon", "/", NOW + 86400000, 1, 1, 1],
  [".news.example.co.uk", "old", "zeta", "/", NOW - 1000, 0, 0, -1],
];

async function fixtureProfile(t, { version = 24 } = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), "hz-chrome-fixture-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(path.join(root, "Local State"), JSON.stringify({ profile: { info_cache: {
    Default: { name: "Ada" }, "Profile 2": { name: "Work" }, "Profile 9": { name: "Gone" }, "../evil": { name: "x" } } } }));
  for (const dir of ["Default/Network", "Profile 2"]) await mkdir(path.join(root, dir), { recursive: true });
  const rows = COOKIES.map(([host, name, value, cookiePath, expires, secure, httponly, samesite]) =>
    `('${host}', '${name}', '', X'${encrypt(value, host, version).toString("hex")}', '${cookiePath}', ` +
    `${expires ? chromeTime(expires) : 0}, ${secure}, ${httponly}, ${samesite}, ${expires ? 1 : 0}, ${expires ? 1 : 0})`);
  const sql = `CREATE TABLE meta(key TEXT, value TEXT); INSERT INTO meta VALUES ('version', '${version}');
    CREATE TABLE cookies(host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT, expires_utc INTEGER,
      is_secure INTEGER, is_httponly INTEGER, samesite INTEGER, has_expires INTEGER, is_persistent INTEGER);
    INSERT INTO cookies VALUES ${rows.join(", ")};`;
  for (const file of [path.join(root, "Default/Network/Cookies"), path.join(root, "Profile 2/Cookies")])
    execFileSync("sqlite3", [file, sql]);
  return root;
}

function fakeSession() {
  const jar = [];
  const key = (c) => `${c.name}|${String(c.domain).replace(/^\./, "")}|${c.path}`;
  return {
    jar,
    cookies: {
      async set(details) {
        if (details.sameSite === "no_restriction" && !details.secure) throw new Error("refused");
        const cookie = { ...details, domain: details.domain ?? new URL(details.url).hostname };
        const at = jar.findIndex((c) => key(c) === key(cookie));
        if (at >= 0) jar.splice(at, 1);
        jar.push(cookie);
      },
      async get() { return jar.map((c) => ({ ...c })); },
      async remove(url, name) {
        const host = new URL(url).hostname;
        for (let i = jar.length - 1; i >= 0; i--)
          if (jar[i].name === name && host.endsWith(String(jar[i].domain).replace(/^\./, ""))) jar.splice(i, 1);
      },
    },
  };
}

test("decrypts v10 values, strips and checks the schema-24 host hash", () => {
  const key = deriveKey(PASSWORD);
  assert.equal(decryptValue(encrypt("hello", ".a.com", 24), key, { version: 24, host: ".a.com" }), "hello");
  assert.equal(decryptValue(encrypt("hello", ".a.com", 23), key, { version: 23, host: ".a.com" }), "hello");
  // A value moved to another site's row fails the hash check.
  assert.equal(decryptValue(encrypt("hello", ".a.com", 24), key, { version: 24, host: ".b.com" }), null);
  const v11 = encrypt("eleven", "x.com", 24);
  v11.write("v11", 0, "latin1");
  assert.equal(decryptValue(v11, key, { version: 24, host: "x.com" }), "eleven");
  assert.equal(decryptValue(encrypt("hello", "x.com", 24, "wrong"), key, { version: 24, host: "x.com" }), null);
  assert.equal(decryptValue(Buffer.from("v20abcdefabcdefabcdef"), key, { version: 24, host: "x.com" }), null);
});

test("registrable domains and the built-in classifier", () => {
  assert.equal(registrableDomain(".mail.google.com"), "google.com");
  assert.equal(registrableDomain("www.bbc.co.uk"), "bbc.co.uk");
  assert.equal(registrableDomain("example.com"), "example.com");
  assert.equal(registrableDomain("127.0.0.1"), "127.0.0.1");
  assert.equal(classifySite("paypal.com"), "payments");
  assert.equal(classifySite("mybank.de"), "payments");
  assert.equal(classifySite("gmx.de"), "email");
  assert.equal(classifySite("google.com", ["mail.google.com"]), "email");
  assert.equal(classifySite("google.com", ["accounts.google.com"]), "sign-in");
  assert.equal(classifySite("example.com"), null);
});

test("cookie details keep secure, httpOnly, sameSite, host-only and expiry; session mode drops expiry", () => {
  const row = { host_key: ".example.com", name: "sid", path: "/", expires_utc: String(chromeTime(NOW + 1000000)),
    is_secure: 1, is_httponly: 1, samesite: 2, has_expires: 1, is_persistent: 1 };
  const persistent = toCookie(row, "v", { persistent: true, now: NOW });
  assert.deepEqual({ ...persistent, expirationDate: Math.round(persistent.expirationDate) }, { url: "https://example.com/",
    name: "sid", value: "v", path: "/", secure: true, httpOnly: true, sameSite: "strict", domain: ".example.com",
    expirationDate: Math.round((NOW + 1000000) / 1000) });
  assert.equal(toCookie(row, "v", { persistent: false, now: NOW }).expirationDate, undefined);
  const hostOnly = toCookie({ ...row, host_key: "a.example.com", is_secure: 0, samesite: -1 }, "v", { persistent: true, now: NOW });
  assert.equal(hostOnly.domain, undefined);
  assert.equal(hostOnly.url, "http://a.example.com/");
  assert.equal(toCookie({ ...row, samesite: 0, is_secure: 0 }, "v", { persistent: true, now: NOW }), null);
  assert.equal(toCookie({ ...row, name: "__Host-x" }, "v", { persistent: true, now: NOW }), null);
  assert.equal(toCookie(row, "v", { persistent: true, now: NOW + 2000000 }), null);
});

test("reads a copied fixture database: profiles, sites with sensitive ones unchecked, decrypted cookies", { skip: !sqliteAvailable }, async (t) => {
  const root = await fixtureProfile(t);
  const profiles = await chromeProfiles(root);
  assert.deepEqual(profiles.map((p) => [p.id, p.label]), [["Default", "Google Chrome — Ada"], ["Profile 2", "Google Chrome — Work"]]);
  const table = await readCookieRows(path.join(root, "Default/Network/Cookies"), { sqlite: "sqlite3" });
  assert.equal(table.version, 24);
  const sites = siteList(table.rows, { now: NOW });
  assert.deepEqual(sites.map((s) => [s.site, s.count, s.category, s.checked]), [
    ["example.com", 2, null, true], ["example.org", 1, null, true],
    ["google.com", 1, "email", false], ["paypal.com", 1, "payments", false]]);
  const { cookies, skipped } = decryptCookies(table, PASSWORD, { sites: ["example.com", "example.org", "paypal.com"], persistent: true, now: NOW });
  assert.equal(skipped, 0);
  assert.deepEqual(cookies.map(({ cookie }) => [cookie.name, cookie.value]), [["sid", "alpha"], ["cart", "beta"],
    ["__Host-token", "gamma"], ["pp", "delta"]]);
  assert.equal(decryptCookies(table, "wrong", { sites: ["example.com"], persistent: true, now: NOW }).cookies.length, 0);
});

test("the importer writes only into the given partition, logs no value, and manages what it imported", { skip: !sqliteAvailable }, async (t) => {
  const root = await fixtureProfile(t);
  const partition = fakeSession();
  const lines = [];
  let asked = 0;
  const importer = createCookieImport({ session: () => partition, ledgerFile: path.join(root, "ledger.json"), root,
    readRows: (file) => readCookieRows(file, { sqlite: "sqlite3" }), password: async () => { asked += 1; return PASSWORD; },
    log: (line) => lines.push(line), now: () => NOW });
  const sites = await importer.handle({ action: "sites", profile: "Default" });
  assert.equal(asked, 0, "listing sites never unlocks the Keychain");
  assert.ok(!JSON.stringify(sites).includes("alpha"));
  const result = await importer.handle({ action: "import", profile: "Default", sites: ["example.com", "paypal.com"], persistent: false });
  assert.deepEqual(result, { imported: 3, skipped: 0, failed: 0, sites: 2 });
  assert.equal(asked, 1);
  assert.deepEqual(partition.jar.map((c) => [c.name, c.value, c.expirationDate]), [["sid", "alpha", undefined],
    ["cart", "beta", undefined], ["pp", "delta", undefined]]);
  for (const secret of ["alpha", "beta", "delta", PASSWORD]) {
    assert.ok(!lines.join("\n").includes(secret));
    assert.ok(!(await readFile(path.join(root, "ledger.json"), "utf8")).includes(secret));
  }
  assert.deepEqual(JSON.parse(lines[0]).sites, ["example.com", "paypal.com"]);
  assert.deepEqual(await importer.handle({ action: "imported" }), [{ site: "example.com", count: 2 }, { site: "paypal.com", count: 1 }]);
  await importer.handle({ action: "remove", site: "paypal.com" });
  assert.deepEqual(partition.jar.map((c) => c.name), ["sid", "cart"]);
  assert.equal(await importer.dropSessionOnly(), 2);
  assert.equal(partition.jar.length, 0);
  await importer.handle({ action: "import", profile: "Profile 2", sites: ["example.org"], persistent: true });
  assert.ok(partition.jar[0].expirationDate > NOW / 1000);
  assert.equal(await importer.dropSessionOnly(), 0);
  await importer.handle({ action: "remove-all" });
  assert.deepEqual(await importer.handle({ action: "imported" }), []);
});

test("the importer refuses unknown profiles, paths and malformed requests", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "hz-chrome-empty-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const importer = createCookieImport({ session: () => fakeSession(), ledgerFile: path.join(root, "l.json"), root,
    password: async () => assert.fail("never asked") });
  for (const profile of ["../evil", "Default/../../x", "Profile 9", 1])
    await assert.rejects(importer.handle({ action: "sites", profile }), /Unknown profile/);
  await assert.rejects(importer.handle({ action: "import", profile: "Default", sites: ["a.com"], persistent: true, extra: 1 }), /Invalid/);
  await assert.rejects(importer.handle({ action: "import", profile: "Default", sites: ["<x>"], persistent: true }), /Invalid import/);
  await assert.rejects(importer.handle({ action: "values" }), /Invalid/);
});

test("only the dialog's own main frame on the app's file may call the importer", () => {
  const url = "file:///app/cookie-import.html";
  const mainFrame = { url };
  const sender = { mainFrame };
  const dialog = { isDestroyed: () => false, webContents: sender };
  assert.equal(isDialogFrame({ sender, senderFrame: mainFrame }, dialog, url), true);
  assert.equal(isDialogFrame({ sender: { mainFrame }, senderFrame: mainFrame }, dialog, url), false);
  assert.equal(isDialogFrame({ sender, senderFrame: { url } }, dialog, url), false);
  assert.equal(isDialogFrame({ sender, senderFrame: { url: "https://evil.example/" } }, { ...dialog, webContents: sender }, url), false);
  assert.equal(isDialogFrame({ sender, senderFrame: mainFrame }, null, url), false);
});

test("the menu offers import and manage only when the platform has them", () => {
  const views = [];
  const file = (cookies) => appMenuTemplate({ name: "X", send: () => {}, open: () => {}, cookies })
    .find((menu) => menu.label === "File").submenu;
  assert.ok(!file(null).some((item) => /Cookies/.test(item.label ?? "")));
  const items = file((view) => views.push(view));
  items.find((item) => item.label === "Import Cookies from Browser…").click();
  items.find((item) => item.label === "Manage Imported Cookies…").click();
  assert.deepEqual(views, ["import", "manage"]);
  assert.match(importLogLine({ profile: "Default", persistent: true, imported: 1, skipped: 0, failed: 0, sites: new Set(["b", "a"]) }), /"sites":\["a","b"\]/);
});
