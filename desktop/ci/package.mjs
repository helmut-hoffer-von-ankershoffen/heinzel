import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
import { packager } from "@electron/packager";
import { checkNode } from "./node-version.mjs";
import { assertBundleIcon } from "./bundle-icon.mjs";
import { captureRuntime } from "./runtime-inventory.mjs";

const nodeCheck = checkNode();
if (!nodeCheck.ok) throw new Error(nodeCheck.message);
if (nodeCheck.warning) process.stderr.write(`${nodeCheck.warning}\n`);

const root = fileURLToPath(new URL("../", import.meta.url));
const args = process.argv.slice(2).filter((argument) => argument !== "--");
const platform = args[0] ?? process.platform;
const arch = args[1] ?? process.arch;
if (!new Set(["darwin", "linux"]).has(platform)) throw new Error("Supported targets: darwin, linux");
if (!new Set(["x64", "arm64"]).has(arch)) throw new Error("Supported architectures: x64, arm64");
const runtime = await captureRuntime(root);
const { version, electronVersion } = runtime;
if (!electronVersion) throw new Error("Could not read the pinned Electron version from package.json");

const output = path.join(root, "dist");
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
const staging = await mkdtemp(path.join(os.tmpdir(), "heinzel-desktop-package-"));
try {
  await mkdir(path.join(staging, "src"));
  for (const [name, bytes] of runtime.files) await writeFile(path.join(staging, name), bytes);
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
    // @electron/packager appends ".icns" and writes it as the bundle icon; without it Electron's atom shows.
    icon: path.join(root, "assets", "heinzel"),
    appBundleId: "org.heinzelmaennchen.desktop",
  });
  for (const appPath of appPaths) {
    if (platform === "darwin") {
      // Packager renames the helpers and rewrites Info.plist, which leaves only the linker's per-binary
      // signatures and no bundle seal. Apple silicon then calls the quarantined app "damaged". Seal the
      // final bundle ad hoc (no identity, no trust claim) so macOS sees an intact, merely unnotarized app.
      const bundle = path.join(appPath, "Heinzelmaennchen.app");
      await assertBundleIcon(bundle);
      execFileSync("/usr/bin/codesign", ["--force", "--deep", "--sign", "-", bundle], { stdio: "inherit" });
      execFileSync("/usr/bin/codesign", ["--verify", "--deep", "--strict", "--verbose=2", bundle], {
        stdio: "inherit",
      });
    }
    process.stdout.write(`Packaged ${appPath}\n`);
  }
} finally {
  await rm(staging, { recursive: true, force: true });
}
