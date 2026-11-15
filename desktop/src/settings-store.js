import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";

/** The desktop app's own choices, with their defaults: nothing else is read from or written to the file. */
export const DEFAULTS = Object.freeze({ linksInBrowser: true });

/**
 * A small settings file in the app's own data folder (`file`), owned by the main process. A missing, unreadable or
 * malformed file means the defaults; a value of the wrong type is ignored; a write that fails keeps the choice for this
 * run and is reported to the caller as false. Writes replace the file whole, so a crash never leaves half of it.
 */
export function createSettingsStore({ file, read = readFileSync, write = writeFileSync, rename = renameSync, mkdir = mkdirSync }) {
  let values = { ...DEFAULTS };
  try {
    const stored = JSON.parse(read(file, "utf8"));
    for (const [key, fallback] of Object.entries(DEFAULTS))
      if (stored && typeof stored === "object" && typeof stored[key] === typeof fallback) values[key] = stored[key];
  } catch { values = { ...DEFAULTS }; }
  return {
    get: (key) => (Object.hasOwn(DEFAULTS, key) ? values[key] : undefined),
    /** Returns whether the choice was saved to disk; it applies in this run either way. */
    set(key, value) {
      if (!Object.hasOwn(DEFAULTS, key) || typeof value !== typeof DEFAULTS[key]) throw new Error("Unknown setting.");
      values[key] = value;
      try {
        mkdir(path.dirname(file), { recursive: true });
        const temporary = `${file}.tmp`;
        write(temporary, `${JSON.stringify(values)}\n`, { mode: 0o600 });
        rename(temporary, file);
        return true;
      } catch { return false; }
    },
  };
}
