import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";

/** Remote input is never a local service address or a command. Only a root HTTPS sign-in link is accepted. */
export function parseWorkspaceLink(input, { requireToken = true } = {}) {
  if (typeof input !== "string" || input.length > 4096 || /[\u0000-\u001f\u007f\\]/.test(input))
    throw new Error("Enter the full HTTPS sign-in link from the host.");
  if (!/^https:\/\/[^/?#]+\/?(?:#token=[A-Z0-9_-]{32,})?$/i.test(input.trim()))
    throw new Error("Enter the full HTTPS sign-in link from the host.");
  let url;
  try { url = new URL(input.trim()); } catch { throw new Error("Enter the full HTTPS sign-in link from the host."); }
  const token = /^#token=([A-Za-z0-9_-]{32,})$/.exec(url.hash)?.[1];
  if (url.protocol !== "https:" || !url.hostname || url.port === "0" || url.username || url.password || url.search ||
      url.pathname !== "/" || (url.hash && !token) || (requireToken && !token))
    throw new Error("Enter the full HTTPS sign-in link from the host.");
  return Object.freeze({ origin: url.origin, token: token ?? null, remote: true });
}

/** A shared address identifies a workspace; it never supplies or redirects a credential. */
export function parseSharedWorkspace(input) {
  if (typeof input !== "string" || input.length > 4096 || /[\u0000-\u001f\u007f\\]/.test(input)) throw new Error("Invalid workspace link.");
  let url;
  try { url = new URL(input.trim()); } catch { throw new Error("Invalid workspace link."); }
  if (url.username || url.password || url.port) throw new Error("Invalid workspace link.");
  let parameters;
  if (url.protocol === "heinzel:" && url.hostname === "connect" && url.pathname === "" && !url.hash)
    parameters = url.searchParams;
  else if (url.protocol === "https:" && url.hostname === "heinzel.team" && url.pathname === "/open/" && !url.search && url.hash && !url.hash.includes("?"))
    parameters = new URLSearchParams(url.hash.slice(1));
  else throw new Error("Invalid workspace link.");
  const entries = [...parameters];
  if (entries.length !== 1 || entries[0][0] !== "server") throw new Error("Invalid workspace link.");
  const target = parseWorkspaceLink(entries[0][1], { requireToken: false });
  if (target.token) throw new Error("Invalid workspace link.");
  return target.origin;
}

export function workspaceName(origin) {
  const host = new URL(origin).hostname;
  if (!host.endsWith(".ts.net") && !host.endsWith(".local")) return host;
  return host.split(".")[0].split("-").map((word) => word.toLowerCase() === "macbook"
    ? "MacBook" : word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

/** No plaintext fallback, including Electron's Linux basic_text provider. Tokens never leave main. */
export function createWorkspaceDirectory({ file, safeStorage, read = readFileSync, write = writeFileSync,
  rename = renameSync, mkdir = mkdirSync, now = Date.now }) {
  const secure = () => safeStorage.isEncryptionAvailable() && safeStorage.getSelectedStorageBackend?.() !== "basic_text";
  let entries = [];
  const transient = new Map();
  try {
    const data = JSON.parse(read(file, "utf8"));
    if (Array.isArray(data)) entries = data.slice(0, 128).flatMap((entry) => {
      try {
        const { origin } = parseWorkspaceLink(entry.origin, { requireToken: false });
        if (origin !== entry.origin || !Number.isFinite(entry.lastUsed)) return [];
        return [{ origin, version: typeof entry.version === "string" ? entry.version : null, lastUsed: entry.lastUsed, encrypted: typeof entry.encrypted === "string" ? entry.encrypted : null }];
      } catch { return []; }
    });
  } catch { /* A missing or damaged directory starts empty. */ }
  function persist() {
    try {
      mkdir(path.dirname(file), { recursive: true });
      write(`${file}.tmp`, JSON.stringify(entries), { mode: 0o600 });
      rename(`${file}.tmp`, file);
      return true;
    } catch { return false; }
  }
  function connection(origin) {
    const entry = entries.find((item) => item.origin === origin);
    if (!entry) return null;
    try {
      let token = transient.get(origin);
      if (!token && entry.encrypted && secure()) {
        const saved = JSON.parse(safeStorage.decryptString(Buffer.from(entry.encrypted, "base64")));
        if (saved.origin !== origin) return null;
        token = saved.token;
      }
      return token ? parseWorkspaceLink(`${origin}/#token=${token}`) : null;
    } catch { return null; }
  }
  return {
    list: () => entries.map(({ origin, lastUsed, version, encrypted }) => ({ origin, ...(version ? { version } : {}), name: workspaceName(origin), lastUsed,
      signedIn: transient.has(origin) || Boolean(encrypted) })),
    connection,
    hasCredential: (origin) => Boolean(entries.find((entry) => entry.origin === origin)?.encrypted),
    remember(value, version) {
      const checked = parseWorkspaceLink(`${value.origin}/#token=${value.token}`);
      transient.set(checked.origin, checked.token);
      let encrypted = null;
      try { if (secure()) encrypted = safeStorage.encryptString(JSON.stringify(checked)).toString("base64"); } catch { /* Session only. */ }
      const previous = entries.find((item) => item.origin === checked.origin);
      entries = [{ origin: checked.origin, version: version ?? previous?.version ?? null, lastUsed: now(), encrypted: encrypted ?? previous?.encrypted ?? null }, ...entries.filter((item) => item.origin !== checked.origin)].slice(0, 128);
      return persist() && encrypted !== null;
    },
    forget(origin) {
      const previous = entries;
      entries = entries.filter((item) => item.origin !== origin);
      if (!persist()) { entries = previous; return false; }
      transient.delete(origin);
      return true;
    },
  };
}
