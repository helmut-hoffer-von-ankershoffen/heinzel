import { execFileSync } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import path from "node:path";

/** The Info.plist's CFBundleIconFile, with the ".icns" macOS assumes when it is left off. */
export function iconFileName(plistXml) {
  const match = /<key>CFBundleIconFile<\/key>\s*<string>([^<]+)<\/string>/.exec(plistXml);
  if (!match) throw new Error("Info.plist has no CFBundleIconFile");
  const name = match[1].trim();
  return name.endsWith(".icns") ? name : `${name}.icns`;
}

/** Fails when the bundle's Info.plist names an icon that is not in Contents/Resources. */
export async function assertBundleIcon(bundle) {
  const plist = path.join(bundle, "Contents", "Info.plist");
  let xml = await readFile(plist, "latin1");
  if (xml.startsWith("bplist")) xml = execFileSync("/usr/bin/plutil", ["-convert", "xml1", "-o", "-", plist], { encoding: "utf8" });
  const name = iconFileName(xml);
  const icon = path.join(bundle, "Contents", "Resources", name);
  try {
    await access(icon);
  } catch {
    throw new Error(`Info.plist names ${name}, which is missing from Contents/Resources`);
  }
  return icon;
}

/**
 * Extra Info.plist keys: Finder shows the umlaut name while the file name stays ASCII; macOS names the visualizer
 * when it asks to allow system audio (Core Audio tap loopback).
 */
export const EXTEND_INFO = {
  CFBundleDisplayName: "Heinzelmännchen",
  NSAudioCaptureUsageDescription: "The audio visualizer reacts to the sound this Mac plays. Nothing is recorded or uploaded.",
};
