import { access, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { extractFile, listPackage } from "@electron/asar";

const root = new URL("../dist/", import.meta.url);
const distEntries = await readdir(root);
const appName = distEntries.find((name) => name.startsWith("Heinzelmaennchen-"));
if (!appName) throw new Error("No packaged app found in desktop/dist");
const app = new URL(`${appName}/`, root);
const isMac = appName.includes("darwin");
const resources = new URL(isMac ? "Heinzelmaennchen.app/Contents/Resources/" : "resources/", app);
const archivePath = fileURLToPath(new URL("app.asar", resources));
await access(archivePath);
const unpacked = new URL("app.asar.unpacked/", resources);
try {
  await access(unpacked);
  throw new Error("Unexpected unpacked files; package must remain compact");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const packageJson = JSON.parse(extractFile(archivePath, "package.json").toString("utf8"));
if (packageJson.main !== "src/main.js") throw new Error("Unexpected packaged app entry point");
const pyproject = await readFile(path.join(packageRoot, "..", "pyproject.toml"), "utf8");
const expectedVersion = pyproject.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (!expectedVersion || packageJson.version !== expectedVersion) {
  throw new Error(`Packaged app version ${packageJson.version} does not match core ${expectedVersion ?? "unknown"}`);
}
const archiveEntries = listPackage(archivePath, { isPack: false }).map((entry) => entry.replace(/^\//, ""));
for (const required of ["package.json", "src/main.js", "src/preload.cjs", "src/ui-navigation.js"]) {
  if (!archiveEntries.includes(required)) throw new Error(`Packaged archive is missing ${required}`);
}
for (const forbidden of ["test/", "e2e/", "ci/"]) {
  if (archiveEntries.some((entry) => entry === forbidden || entry.startsWith(forbidden))) {
    throw new Error(`Unexpected development files in app archive: ${forbidden}`);
  }
}
console.log(`Verified app.asar for ${appName} at core v${expectedVersion}; unsigned artifacts only.`);
