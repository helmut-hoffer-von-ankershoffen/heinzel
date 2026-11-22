// Reading cookies from the person's own Google Chrome profiles on macOS, for "Import cookies from your browser".
// Everything here runs in the desktop main process: values never reach a page, the hz service or the network.
import { createDecipheriv, createHash, pbkdf2Sync } from "node:crypto";
import { execFile } from "node:child_process";
import { copyFile, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

/** Chrome's own data folder on macOS; each profile is a folder in it, named by `Local State`. */
export const chromeRoot = (home = os.homedir()) => path.join(home, "Library", "Application Support", "Google", "Chrome");
const PROFILE_DIR = /^(Default|Profile \d{1,4}|Guest Profile|System Profile)$/;
// Chrome's cookie database schema 24 (Chrome 130) puts SHA-256 of the cookie's host before the value, then encrypts.
const DOMAIN_HASH_VERSION = 24;
// Seconds between 1601-01-01 (Chrome's epoch, in microseconds) and 1970-01-01.
const WINDOWS_EPOCH_OFFSET = 11644473600;
const MAX_ROWS = 50000;
const SAME_SITE = new Map([[-1, "unspecified"], [0, "no_restriction"], [1, "lax"], [2, "strict"]]);

/** The profiles of a Chrome install, named as Chrome names them. Reads only `Local State` and folder names. */
export async function chromeProfiles(root = chromeRoot()) {
  let state = {};
  try { state = JSON.parse(await readFile(path.join(root, "Local State"), "utf8")); } catch { return []; }
  const cache = state?.profile?.info_cache;
  if (!cache || typeof cache !== "object") return [];
  const profiles = [];
  for (const [dir, info] of Object.entries(cache)) {
    if (!PROFILE_DIR.test(dir) || dir === "System Profile" || dir === "Guest Profile") continue;
    const database = await cookieDatabase(path.join(root, dir));
    if (!database) continue;
    const name = typeof info?.name === "string" && info.name.trim() ? info.name.trim().slice(0, 80) : dir;
    profiles.push({ id: dir, name, label: `Google Chrome — ${name}` });
  }
  return profiles.sort((a, b) => (a.id === "Default" ? -1 : b.id === "Default" ? 1 : a.id.localeCompare(b.id)));
}

/** Chrome 96 and later keep cookies in `Network/Cookies`; older profiles in `Cookies`. */
export async function cookieDatabase(profileDir) {
  for (const file of [path.join(profileDir, "Network", "Cookies"), path.join(profileDir, "Cookies")]) {
    try { if ((await stat(file)).isFile()) return file; } catch { /* not this layout */ }
  }
  return null;
}

/** Whether a profile id is one `chromeProfiles` could have returned; nothing else names a folder to read. */
export const isProfileId = (id) => typeof id === "string" && PROFILE_DIR.test(id) && id !== "System Profile" && id !== "Guest Profile";

const run = (file, args, options = {}) => new Promise((resolve, reject) => {
  execFile(file, args, { maxBuffer: 256 * 1024 * 1024, timeout: 60000, ...options }, (error, stdout) => {
    if (error) reject(error); else resolve(stdout);
  });
});

/**
 * Copies the database (and its write-ahead log) into a private folder first: Chrome may hold a lock on it, and
 * nothing here ever writes to Chrome's own files. The copy is removed before this returns.
 */
export async function readCookieRows(database, { sqlite = "/usr/bin/sqlite3", exec = run } = {}) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "hz-cookie-import-"));
  try {
    const copy = path.join(dir, "Cookies");
    await copyFile(database, copy);
    for (const suffix of ["-wal", "-journal"]) {
      try { await copyFile(database + suffix, copy + suffix); } catch { /* no log beside it */ }
    }
    const query = (sql) => exec(sqlite, ["-json", copy, sql]).then((out) => (out.trim() ? JSON.parse(out) : []));
    const meta = await query("SELECT value FROM meta WHERE key = 'version'");
    const version = Number.parseInt(meta[0]?.value ?? "0", 10) || 0;
    const rows = await query(`SELECT host_key, name, value, hex(encrypted_value) AS encrypted, path, expires_utc,
      is_secure, is_httponly, samesite, has_expires, is_persistent FROM cookies LIMIT ${MAX_ROWS}`);
    return { version, rows };
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

/** The password Chrome keeps in the login Keychain. macOS asks the person first; nothing here stores it. */
export async function chromeSafeStoragePassword({ exec = run } = {}) {
  const out = await exec("/usr/bin/security", ["find-generic-password", "-w", "-s", "Chrome Safe Storage", "-a", "Chrome"]);
  const password = out.replace(/\n$/, "");
  if (!password) throw new Error("Google Chrome's cookies could not be unlocked.");
  return password;
}

/** Chrome's macOS key: PBKDF2-SHA1 of the Keychain password, salt "saltysalt", 1003 rounds, 16 bytes. */
export const deriveKey = (password) => pbkdf2Sync(password, "saltysalt", 1003, 16, "sha1");

/**
 * The plaintext of one encrypted value, or null when it cannot be trusted. v10 (macOS) and v11 (the same scheme
 * elsewhere) are AES-128-CBC with an IV of 16 spaces. From schema 24 the plaintext starts with SHA-256(host_key),
 * which must match the row's host: a value copied from another site's row is refused.
 */
export function decryptValue(encrypted, key, { version, host }) {
  if (!Buffer.isBuffer(encrypted) || encrypted.length < 3 + 16) return null;
  const prefix = encrypted.subarray(0, 3).toString("latin1");
  if (prefix !== "v10" && prefix !== "v11") return null;
  let plain;
  try {
    const decipher = createDecipheriv("aes-128-cbc", key, Buffer.alloc(16, 0x20));
    plain = Buffer.concat([decipher.update(encrypted.subarray(3)), decipher.final()]);
  } catch { return null; }
  try {
    if (version >= DOMAIN_HASH_VERSION) {
      if (plain.length < 32 || !plain.subarray(0, 32).equals(createHash("sha256").update(host).digest())) return null;
      plain = plain.subarray(32);
    }
    const text = plain.toString("utf8");
    return Buffer.from(text, "utf8").equals(plain) ? text : null;
  } finally { plain.fill(0); }
}

// Suffixes under which a registrable domain has three labels: a short, built-in list for the common cases.
const SECOND_LEVEL = new Set(["co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "com.au", "net.au", "org.au", "co.jp",
  "ne.jp", "or.jp", "co.nz", "co.za", "com.br", "com.mx", "com.cn", "com.tr", "co.in", "co.kr", "com.sg", "com.hk",
  "co.at", "or.at", "com.ar", "co.il", "com.tw", "github.io", "herokuapp.com", "vercel.app", "netlify.app",
  "pages.dev", "workers.dev", "appspot.com", "cloudfront.net", "azurewebsites.net"]);

/** The site a cookie host belongs to ("mail.google.com" -> "google.com"), from a small built-in suffix list. */
export function registrableDomain(host) {
  const name = String(host ?? "").toLowerCase().replace(/^\./, "").replace(/\.$/, "");
  if (!name || /^[\d.]+$/.test(name) || name.includes(":")) return name;
  const labels = name.split(".");
  if (labels.length <= 2) return name;
  const tail = labels.slice(-2).join(".");
  return labels.slice(SECOND_LEVEL.has(tail) ? -3 : -2).join(".");
}

/** Sensitive categories start unchecked in the site list: a person picks them on purpose. */
export const SENSITIVE = Object.freeze(["email", "payments", "sign-in"]);
export const CATEGORY_LABELS = Object.freeze({ email: "Email", payments: "Bank or payments", "sign-in": "Sign-in provider" });
const SIGN_IN = new Set(["google.com", "apple.com", "icloud.com", "microsoft.com", "microsoftonline.com", "live.com",
  "okta.com", "auth0.com", "onelogin.com", "duosecurity.com", "facebook.com", "github.com", "gitlab.com", "login.gov",
  "amazon.com", "yahoo.com", "x.com", "twitter.com", "linkedin.com", "1password.com", "bitwarden.com", "lastpass.com",
  "id.me", "jumpcloud.com", "pingidentity.com", "auth.gov", "salesforce.com", "slack.com", "atlassian.com"]);
const EMAIL = new Set(["gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "office.com", "office365.com",
  "proton.me", "protonmail.com", "fastmail.com", "zoho.com", "gmx.de", "gmx.net", "gmx.com", "web.de", "t-online.de",
  "posteo.de", "mailbox.org", "tutanota.com", "tuta.com", "aol.com", "mail.com", "mail.ru", "yandex.com", "yandex.ru",
  "hey.com", "superhuman.com"]);
const PAYMENTS = new Set(["paypal.com", "paypal.me", "stripe.com", "wise.com", "revolut.com", "n26.com", "klarna.com",
  "chase.com", "bankofamerica.com", "wellsfargo.com", "citi.com", "capitalone.com", "americanexpress.com", "amex.com",
  "discover.com", "usbank.com", "schwab.com", "fidelity.com", "vanguard.com", "robinhood.com", "venmo.com",
  "cash.app", "squareup.com", "adyen.com", "coinbase.com", "kraken.com", "binance.com", "monzo.com", "starlingbank.com",
  "barclays.co.uk", "hsbc.com", "hsbc.co.uk", "lloydsbank.com", "natwest.com", "santander.com", "ing.com", "ing.de",
  "dkb.de", "comdirect.de", "consorsbank.de", "postbank.de", "deutsche-bank.de", "db.com", "commerzbank.de",
  "sparkasse.de", "volksbank.de", "vr.de", "targobank.de", "trade-republic.com", "traderepublic.com", "scalable.capital",
  "bunq.com", "ubs.com", "credit-suisse.com", "postfinance.ch", "raiffeisen.ch", "apple.cash", "affirm.com",
  "afterpay.com", "mollie.com", "braintreegateway.com", "paddle.com", "lemonsqueezy.com"]);
const PAYMENT_WORDS = /bank|sparkasse|raiffeisen|credit|wallet|finanz|broker|^pay|pay$/;

/** The category of a site, or null; email hosts of sign-in providers (mail.google.com) count as email. */
export function classifySite(site, hosts = []) {
  if (PAYMENTS.has(site) || PAYMENT_WORDS.test(site.split(".")[0])) return "payments";
  if (EMAIL.has(site)) return "email";
  if (hosts.some((host) => /^\.?(mail|outlook|webmail|inbox)\./.test(host))) return "email";
  if (SIGN_IN.has(site)) return "sign-in";
  return null;
}

const asNumber = (value) => (typeof value === "number" ? value : Number.parseInt(String(value ?? "0"), 10) || 0);

/** The sites in a profile, without reading any value: name, category, count, and whether it starts checked. */
export function siteList(rows, { now = Date.now() } = {}) {
  const sites = new Map();
  for (const row of rows) {
    if (isExpired(row, now)) continue;
    const site = registrableDomain(row.host_key);
    if (!site) continue;
    const entry = sites.get(site) ?? { site, count: 0, hosts: new Set() };
    entry.count += 1;
    entry.hosts.add(String(row.host_key));
    sites.set(site, entry);
  }
  return [...sites.values()].map(({ site, count, hosts }) => {
    const category = classifySite(site, [...hosts]);
    return { site, count, category, checked: !SENSITIVE.includes(category) };
  }).sort((a, b) => a.site.localeCompare(b.site));
}

const expiresAt = (row) => asNumber(row.expires_utc) / 1e6 - WINDOWS_EPOCH_OFFSET;
const isPersistentRow = (row) => asNumber(row.has_expires) === 1 || asNumber(row.is_persistent) === 1;
const isExpired = (row, now) => isPersistentRow(row) && asNumber(row.expires_utc) > 0 && expiresAt(row) * 1000 <= now;

/**
 * One Chrome row as the `cookies.set` details of Electron, or null. Host-only cookies keep no domain, `__Host-`
 * cookies must be secure on "/" with no domain, `SameSite=None` must be secure. With `persistent` false every
 * cookie is a session cookie, which the browser forgets when the app quits.
 */
export function toCookie(row, value, { persistent, now = Date.now() }) {
  const host = String(row.host_key ?? "").toLowerCase();
  const name = String(row.name ?? "");
  if (!host || !name || isExpired(row, now)) return null;
  const secure = asNumber(row.is_secure) === 1;
  const hostOnly = !host.startsWith(".");
  const bare = host.replace(/^\./, "");
  const cookiePath = typeof row.path === "string" && row.path.startsWith("/") ? row.path : "/";
  const sameSite = SAME_SITE.get(asNumber(row.samesite)) ?? "unspecified";
  if (name.startsWith("__Host-") && (!secure || !hostOnly || cookiePath !== "/")) return null;
  if (name.startsWith("__Secure-") && !secure) return null;
  if (sameSite === "no_restriction" && !secure) return null;
  const cookie = { url: `${secure ? "https" : "http"}://${bare}${cookiePath}`, name, value, path: cookiePath, secure,
    httpOnly: asNumber(row.is_httponly) === 1, sameSite };
  if (!hostOnly) cookie.domain = host;
  if (persistent && isPersistentRow(row) && asNumber(row.expires_utc) > 0) cookie.expirationDate = expiresAt(row);
  return cookie;
}

/**
 * The cookies of the chosen sites, decrypted with a key derived once and wiped before this returns. A row whose
 * value cannot be decrypted is skipped and counted, never guessed.
 */
export function decryptCookies({ version, rows }, password, { sites, persistent, now = Date.now() }) {
  const wanted = new Set(sites);
  const key = deriveKey(password);
  const cookies = [];
  let skipped = 0;
  try {
    for (const row of rows) {
      const site = registrableDomain(row.host_key);
      if (!wanted.has(site)) continue;
      let value = typeof row.value === "string" && row.value ? row.value : null;
      if (value === null && row.encrypted)
        value = decryptValue(Buffer.from(String(row.encrypted), "hex"), key, { version, host: String(row.host_key) });
      const cookie = value === null ? null : toCookie(row, value, { persistent, now });
      if (cookie) cookies.push({ site, cookie }); else skipped += 1;
    }
  } finally { key.fill(0); }
  return { cookies, skipped };
}

/** The one audit line of an import: counts and site names, never a cookie name or value. */
export function importLogLine({ profile, persistent, imported, skipped, failed, sites }) {
  return JSON.stringify({ event: "desktop.cookies.import", profile, persistent, imported, skipped, failed,
    sites: [...sites].sort() });
}
