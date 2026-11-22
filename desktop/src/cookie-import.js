// "Import cookies from your browser": the main-process side of the dialog. The dialog only ever sees profile names,
// site names, categories and counts; cookie values stay in this process and go only into the browser's partition.
import { readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromeProfiles, chromeRoot, chromeSafeStoragePassword, cookieDatabase, decryptCookies, importLogLine,
  isProfileId, readCookieRows, siteList } from "./chrome-cookies.js";

const SITE = /^[a-z0-9.-]{1,253}$/;
const MAX_SITES = 5000;
// The requests of the dialog, each with exactly these keys.
const FIELDS = { profiles: ["action"], sites: ["action", "profile"], import: ["action", "profile", "sites", "persistent"],
  imported: ["action"], remove: ["action", "site"], "remove-all": ["action"] };
const exact = (request, keys) => Object.keys(request).length === keys.length && keys.every((key) => Object.hasOwn(request, key));
const bareDomain = (domain) => String(domain ?? "").replace(/^\./, "");
const sameCookie = (a, b) => a.name === b.name && a.path === b.path && bareDomain(a.domain) === bareDomain(b.domain);

/**
 * `session()` is the browser's website partition. `password()` asks macOS for Chrome's Keychain password; it is
 * called once per import, after the person pressed Import, and the password and key are dropped right after.
 */
export function createCookieImport({ session, ledgerFile, root = chromeRoot(), readRows = readCookieRows,
  password = () => chromeSafeStoragePassword(), log = (line) => console.info(line), now = () => Date.now() }) {
  let busy = false;
  // The ledger names what was imported (site, cookie name, domain, path), never a value; it lets the person remove it.
  const readLedger = async () => {
    try {
      const data = JSON.parse(await readFile(ledgerFile, "utf8"));
      return Array.isArray(data?.cookies) ? data.cookies.filter((entry) => entry && typeof entry.site === "string") : [];
    } catch { return []; }
  };
  const writeLedger = async (cookies) => {
    const temporary = `${ledgerFile}.${process.pid}.tmp`;
    await writeFile(temporary, `${JSON.stringify({ cookies })}\n`, { mode: 0o600 });
    await rename(temporary, ledgerFile);
  };
  // Imported cookies still in the partition: a clear, an expiry or the site itself may have removed some.
  const present = async () => {
    const ledger = await readLedger();
    if (!ledger.length) return [];
    const jar = await session().cookies.get({});
    return ledger.filter((entry) => jar.some((cookie) => sameCookie(entry, cookie)));
  };
  const removeEntries = async (entries) => {
    const results = await Promise.allSettled(entries.map((entry) => session().cookies.remove(entry.url, entry.name)));
    return results.filter((result) => result.status === "rejected").length;
  };
  // Only a profile Chrome itself lists is read; the id never becomes a path any other way.
  const profileDatabase = async (profile) => {
    if (!isProfileId(profile) || !(await chromeProfiles(root)).some((entry) => entry.id === profile))
      throw new Error("Unknown profile.");
    const database = await cookieDatabase(path.join(root, profile));
    if (!database) throw new Error("Unknown profile.");
    return database;
  };

  const importSites = async ({ profile, sites, persistent }) => {
    if (!Array.isArray(sites) || sites.length === 0 || sites.length > MAX_SITES ||
        !sites.every((site) => typeof site === "string" && SITE.test(site)) || typeof persistent !== "boolean")
      throw new Error("Invalid import.");
    const table = await readRows(await profileDatabase(profile));
    let secret = await password();
    let decrypted;
    try { decrypted = decryptCookies(table, secret, { sites, persistent, now: now() }); } finally { secret = null; }
    const { cookies, skipped } = decrypted;
    const added = [];
    let failed = 0;
    for (const { site, cookie } of cookies) {
      try {
        await session().cookies.set(cookie);
        added.push({ site, name: cookie.name, domain: cookie.domain ?? new URL(cookie.url).hostname, path: cookie.path,
          url: cookie.url, session: cookie.expirationDate === undefined });
      } catch { failed += 1; }
      cookie.value = "";
    }
    const ledger = await readLedger();
    await writeLedger([...ledger.filter((old) => !added.some((entry) => sameCookie(old, entry))), ...added]);
    const imported = new Set(added.map((entry) => entry.site));
    log(importLogLine({ profile, persistent, imported: added.length, skipped, failed, sites: imported }));
    return { imported: added.length, skipped, failed, sites: imported.size };
  };

  const removeSites = async (site) => {
    const ledger = await readLedger();
    const chosen = ledger.filter((entry) => site === null || entry.site === site);
    const failed = await removeEntries(chosen);
    await writeLedger(ledger.filter((entry) => !chosen.includes(entry)));
    log(JSON.stringify({ event: "desktop.cookies.remove", sites: [...new Set(chosen.map((entry) => entry.site))].sort(),
      removed: chosen.length - failed, failed }));
    return { removed: chosen.length - failed, failed };
  };

  const run = async (request) => {
    const { action } = request;
    if (action === "profiles") return (await chromeProfiles(root)).map(({ id, label }) => ({ id, label }));
    if (action === "sites") {
      const { rows } = await readRows(await profileDatabase(request.profile));
      return siteList(rows, { now: now() });
    }
    if (action === "imported") {
      const counts = new Map();
      for (const entry of await present()) counts.set(entry.site, (counts.get(entry.site) ?? 0) + 1);
      return [...counts].map(([site, count]) => ({ site, count })).sort((a, b) => a.site.localeCompare(b.site));
    }
    if (action === "remove") {
      if (typeof request.site !== "string" || !SITE.test(request.site)) throw new Error("Unknown site.");
      return removeSites(request.site);
    }
    if (action === "remove-all") return removeSites(null);
    return importSites(request);
  };

  return {
    /** One dialog request. Only the import dialog's own frame may call this (see `isDialogFrame`). */
    async handle(request) {
      if (!request || typeof request !== "object" || typeof request.action !== "string" ||
          !Object.hasOwn(FIELDS, request.action) || !exact(request, FIELDS[request.action]))
        throw new Error("Invalid cookie request.");
      if (busy) throw new Error("An import is already running.");
      busy = true;
      try { return await run(request); } finally { busy = false; }
    },
    /** Session-only imports end with the app: removed on quit, and again at start in case the app crashed. */
    async dropSessionOnly() {
      const ledger = await readLedger();
      const sessionOnly = ledger.filter((entry) => entry.session);
      if (!sessionOnly.length) return 0;
      await removeEntries(sessionOnly);
      await writeLedger(ledger.filter((entry) => !entry.session));
      return sessionOnly.length;
    },
  };
}

/** Whether an IPC event comes from the import dialog's own main frame, loaded from the app's own file. */
export function isDialogFrame(event, dialog, url) {
  try {
    if (!dialog || dialog.isDestroyed() || event.sender !== dialog.webContents) return false;
    const frame = event.senderFrame;
    return Boolean(frame && frame === event.sender.mainFrame && frame.url === url);
  } catch { return false; }
}
