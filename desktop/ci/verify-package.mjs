import { access, lstat, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { verifyRuntime } from "./runtime-inventory.mjs";

const root = new URL("../dist/", import.meta.url);
const distEntries = await readdir(root);
if (distEntries.length !== 1 || !/^Heinzelmaennchen-(darwin|linux)-(x64|arm64)$/.test(distEntries[0]))
  throw new Error("Expected exactly one supported packaged app in desktop/dist.");
const appName = distEntries[0];
const app = new URL(`${appName}/`, root);
const appInfo = await lstat(app);
if (!appInfo.isDirectory() || appInfo.isSymbolicLink()) throw new Error("Packaged app directory is unsafe.");
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
const expectedVersion = await verifyRuntime(archivePath, packageRoot);
console.log(`Verified exact app.asar runtime for ${appName} at core v${expectedVersion}; unsigned artifacts only.`);
