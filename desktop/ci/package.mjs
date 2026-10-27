import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
import { packager } from "@electron/packager";

if (process.versions.node !== "24.19.0") {
  throw new Error(`Electron packaging requires Node.js 24.19.0; found ${process.versions.node}.`);
}

const root = fileURLToPath(new URL("../", import.meta.url));
const args = process.argv.slice(2).filter((argument) => argument !== "--");
const platform = args[0] ?? process.platform;
const arch = args[1] ?? process.arch;
if (!new Set(["darwin", "linux"]).has(platform)) throw new Error("Supported targets: darwin, linux");
if (!new Set(["x64", "arm64"]).has(arch)) throw new Error("Supported architectures: x64, arm64");
const pyproject = await readFile(path.join(root, "..", "pyproject.toml"), "utf8");
const version = pyproject.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (!version) throw new Error("Could not read the core version from pyproject.toml");
const sourcePackage = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
const electronVersion = sourcePackage.devDependencies?.electron;
if (!electronVersion) throw new Error("Could not read the pinned Electron version from package.json");

const output = path.join(root, "dist");
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
const staging = await mkdtemp(path.join(os.tmpdir(), "heinzel-desktop-package-"));
try {
  await cp(path.join(root, "src"), path.join(staging, "src"), { recursive: true });
  await writeFile(path.join(staging, "package.json"), `${JSON.stringify({ ...sourcePackage, version }, null, 2)}\n`);
  const appPaths = await packager({
    dir: staging,
    name: "Heinzelmaennchen",
    platform,
    arch,
    electronVersion,
    appVersion: version,
    out: output,
    overwrite: true,
    asar: true,
    prune: true,
    appBundleId: "org.heinzelmaennchen.desktop",
  });
  for (const appPath of appPaths) process.stdout.write(`Packaged ${appPath}\n`);
} finally {
  await rm(staging, { recursive: true, force: true });
}
