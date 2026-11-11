import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, mkdir, mkdtemp, readdir, readlink, realpath, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifyRuntime } from "./runtime-inventory.mjs";

const appName = "Heinzelmaennchen";
const desktop = fileURLToPath(new URL("../", import.meta.url));
const bundleName = `${appName}.app`;
const platform = "darwin";
const arch = "arm64";
const unsafeMode = 0o7022;

function within(root, target) {
  return target.startsWith(root + path.sep);
}

async function inspectTree(root, directory = root) {
  const names = await readdir(directory);
  const canonicalNames = new Set();
  for (const name of names) {
    const canonical = name.normalize("NFD").toLowerCase();
    if (canonicalNames.has(canonical) || /[\\\x00-\x1f\x7f]/.test(name)) {
      throw new Error(`Unsafe or duplicate app entry: ${name}`);
    }
    canonicalNames.add(canonical);
    const entry = path.join(directory, name);
    const info = await lstat(entry);
    if (info.isSymbolicLink()) {
      const target = await readlink(entry);
      if (path.isAbsolute(target) || /[\\\x00-\x1f\x7f]/.test(target) ||
          target.split("/").includes("..") || !within(root, await realpath(entry))) {
        throw new Error(`Unsafe app symlink: ${entry}`);
      }
    } else if (info.isDirectory()) {
      if (info.mode & unsafeMode) throw new Error(`Unsafe app directory permissions: ${entry}`);
      await inspectTree(root, entry);
    } else if (!info.isFile() || info.nlink !== 1 || (info.mode & unsafeMode)) {
      throw new Error(`Unsafe app file: ${entry}`);
    }
  }
}

const [tag, commit, output] = process.argv.slice(2);
if (process.platform !== platform || process.arch !== arch || process.argv.length !== 5 ||
    !/^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.test(tag) || !/^[a-f0-9]{40}$/.test(commit)) {
  throw new Error("Expected macOS ARM64 and arguments <version tag> <source commit> <new output directory>.");
}
const version = tag.slice(1);
const packaged = path.join(desktop, "dist", `${appName}-${platform}-${arch}`);
if (!(await lstat(packaged)).isDirectory()) throw new Error("Expected a real package output directory.");
const root = path.join(await realpath(packaged), bundleName);
const rootInfo = await lstat(root);
if (!rootInfo.isDirectory() || (rootInfo.mode & unsafeMode)) {
  throw new Error("Expected a real application bundle directory with safe permissions.");
}
await inspectTree(root);
const contents = path.join(root, "Contents");
const plist = path.join(contents, "Info.plist");
if (!(await lstat(plist)).isFile()) throw new Error("Expected a regular bundle Info.plist.");
const plistValue = (key) => execFileSync("/usr/bin/plutil", ["-extract", key, "raw", "-o", "-", plist], {
  encoding: "utf8",
}).trim();
if (plistValue("CFBundleIdentifier") !== "org.heinzelmaennchen.desktop" ||
    plistValue("CFBundleExecutable") !== appName || plistValue("CFBundleShortVersionString") !== version) {
  throw new Error("The app bundle identity does not match the release.");
}
for (const relative of ["MacOS/" + appName, "Frameworks/Electron Framework.framework/Versions/A/Electron Framework"]) {
  const executable = path.join(contents, relative);
  const info = await lstat(executable);
  if (!info.isFile() || !(info.mode & 0o100) ||
      execFileSync("/usr/bin/lipo", ["-archs", executable], { encoding: "utf8" }).trim() !== arch) {
    throw new Error(`Expected a native ARM64 executable: ${relative}`);
  }
}
if (await verifyRuntime(path.join(contents, "Resources", "app.asar"), desktop) !== version) {
  throw new Error("The packaged app version does not match the release.");
}

const verifySeal = (bundle) => execFileSync("/usr/bin/codesign", ["--verify", "--deep", "--strict", "--verbose=2", bundle], {
  stdio: "inherit",
});
verifySeal(root);

await mkdir(output);
const name = `heinzel-desktop-${version}-${platform}-${arch}.tar.gz`;
const archive = path.resolve(output, name);
execFileSync("/usr/bin/tar", [
  "--format=pax", "--no-mac-metadata", "--no-xattrs", "--no-acls", "--no-fflags",
  "-czf", archive, "-C", path.dirname(root), bundleName,
]);
// The seal must survive the archive: extract it the way a user's machine will and verify again.
const unpacked = await mkdtemp(path.join(os.tmpdir(), "heinzel-desktop-seal-"));
try {
  execFileSync("/usr/bin/tar", ["-xzf", archive, "-C", unpacked]);
  verifySeal(path.join(unpacked, bundleName));
} finally {
  await rm(unpacked, { recursive: true, force: true });
}
const hash = createHash("sha256");
for await (const chunk of createReadStream(archive)) hash.update(chunk);
const manifest = {
  schema: 1, version, commit, platform, arch,
  archive: { name, sha256: hash.digest("hex"), size: (await stat(archive)).size },
};
await writeFile(path.join(output, "desktop-artifact-manifest.json"), JSON.stringify(manifest, null, 2) + "\n", {
  flag: "wx",
});
