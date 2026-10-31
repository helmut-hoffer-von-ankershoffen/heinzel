// Publish fixture coordination only after the complete JSON is available on the same filesystem.
import * as fs from "node:fs";
import { dirname, join } from "node:path";

export function publishJson(target, value, io = fs) {
  const owned = io.mkdtempSync(join(dirname(target), ".daemon-publish-"));
  const staged = join(owned, "value.json");
  try {
    io.writeFileSync(staged, JSON.stringify(value), { flag: "wx", mode: 0o600 });
    io.renameSync(staged, target);
  } finally {
    if (io.existsSync(staged)) io.unlinkSync(staged);
    io.rmdirSync(owned);
  }
}
