import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { TITLEBAR_HEIGHT, windowOptions } from "../src/window-options.js";

const main = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
const preload = await readFile(new URL("../src/preload.cjs", import.meta.url), "utf8");

test("macOS hides the native title bar and insets the traffic lights into the 44 px row", () => {
  assert.equal(TITLEBAR_HEIGHT, 44);
  assert.deepEqual(windowOptions("darwin"), {
    width: 1100, height: 760, show: false, title: "Heinzelmännchen",
    titleBarStyle: "hiddenInset", trafficLightPosition: { x: 14, y: 16 },
  });
});

test("Windows and Linux keep the native frame", () => {
  for (const platform of ["win32", "linux"]) {
    const options = windowOptions(platform);
    assert.deepEqual(options, { width: 1100, height: 760, show: false, title: "Heinzelmännchen" });
  }
});

test("the window spreads the platform options and relays full screen to the web", () => {
  assert.match(main, /\.\.\.windowOptions\(process\.platform\),\n\s+webPreferences: \{/);
  assert.match(main, /window\.on\("enter-full-screen", \(\) => window\.webContents\.send\("hz:fullscreen", true\)\)/);
  assert.match(main, /window\.on\("leave-full-screen", \(\) => window\.webContents\.send\("hz:fullscreen", false\)\)/);
});

function loadPreload(platform) {
  const exposed = {};
  const listeners = new Map();
  const invoked = [];
  const ipcRenderer = {
    invoke: (...args) => { invoked.push(args); return Promise.resolve(null); },
    on: (channel, listener) => listeners.set(channel, listener),
    removeListener: (channel, listener) => { if (listeners.get(channel) === listener) listeners.delete(channel); },
  };
  const contextBridge = { exposeInMainWorld: (name, value) => { exposed[name] = value; } };
  runInNewContext(preload, {
    require: (name) => { assert.equal(name, "electron"); return { contextBridge, ipcRenderer }; },
    globalThis: { navigator: { platform } },
  });
  return { bridge: exposed.heinzelDesktop, listeners, invoked };
}

test("the bridge exposes frozen macOS insets and none elsewhere", () => {
  const mac = loadPreload("MacIntel").bridge;
  assert.deepEqual({ ...mac.titlebar }, { height: 44, left: 80, right: 0 });
  assert.ok(Object.isFrozen(mac.titlebar) && Object.isFrozen(mac));
  assert.equal(loadPreload("Win32").bridge.titlebar, null);
  assert.equal(loadPreload("Linux x86_64").bridge.titlebar, null);
});

test("onFullscreen delivers a boolean and unsubscribes", () => {
  const { bridge, listeners } = loadPreload("MacIntel");
  const seen = [];
  const stop = bridge.onFullscreen((full) => seen.push(full));
  listeners.get("hz:fullscreen")({}, true);
  listeners.get("hz:fullscreen")({}, "yes");
  stop();
  assert.equal(listeners.has("hz:fullscreen"), false);
  assert.deepEqual(seen, [true, false]);
});

test("the audio bridge asks main for system loopback and arms only known sources", () => {
  const { bridge, invoked } = loadPreload("MacIntel");
  void bridge.systemAudio();
  for (const source of ["system", "workspace", "screen", undefined]) void bridge.audioSource(source);
  assert.deepEqual(invoked, [["hz:system-audio"], ["hz:audio-source", "system"], ["hz:audio-source", "workspace"],
    ["hz:audio-source", "workspace"], ["hz:audio-source", "workspace"]]);
});
