import { execFile } from "node:child_process";
import { lstatSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { ownedLifecycleWindow } from "./lifecycle-ipc.js";

const clients = new Map([["claude", ".mcp.json"], ["codex", ".codex/config.toml"], ["cursor", ".cursor/mcp.json"]]);
const unconfirmed = "The project configuration could not be confirmed. Check the project before trying again.";

/** The renderer supplies only a client; the local picker and confirmation bind the exact target. */
export function createMcpProjectHandler({ choose, prompt, install, inspect = lstatSync, ...guard }) {
  const pending = new WeakSet();
  return async (event, client) => {
    const { window, connection } = ownedLifecycleWindow(event, guard);
    if (!clients.has(client) || pending.has(window)) throw new Error(unconfirmed);
    pending.add(window);
    try {
      const selection = await choose(window);
      if (selection.canceled || selection.filePaths.length !== 1) return false;
      const project = selection.filePaths[0];
      if (typeof project !== "string" || !isAbsolute(project) || /[\x00-\x1f\x7f]/.test(project)) {
        throw new Error(unconfirmed);
      }
      const info = inspect(project);
      if (!info.isDirectory() || !Number.isSafeInteger(info.dev) || !Number.isSafeInteger(info.ino)) {
        throw new Error(unconfirmed);
      }
      if (ownedLifecycleWindow(event, guard).connection !== connection) throw new Error(unconfirmed);
      const target = join(project, clients.get(client));
      const choice = await prompt(window, {
        type: "question",
        message: `Create the ${client} project MCP configuration?`,
        detail: `Create ${target}. Existing configuration is never replaced. The client connection will not be checked.`,
        buttons: ["Cancel", "Create configuration"], cancelId: 0, defaultId: 0, noLink: true,
      });
      if (choice.response !== 1) return false;
      if (ownedLifecycleWindow(event, guard).connection !== connection) throw new Error(unconfirmed);
      const result = await install(client, project, info.dev, info.ino);
      if (!result || result.configuration !== "created" || result.mcp_readiness !== "unverified" || result.path !== target) {
        throw new Error(unconfirmed);
      }
      return true;
    } catch {
      throw new Error(unconfirmed);
    } finally {
      pending.delete(window);
    }
  };
}

/** Run a fixed CLI operation without a shell; timeout or malformed output is never reported as success. */
export function installMcpProject(executable, client, project, device, inode, run = execFile) {
  return new Promise((resolve, reject) => {
    run(executable, ["system", "serve", "mcp-install-project", "--client", client, "--project", project,
      "--expected-device", String(device), "--expected-inode", String(inode)],
    { timeout: 15_000, maxBuffer: 4096, windowsHide: true, shell: false }, (error, stdout) => {
      if (error) { reject(error); return; }
      try { resolve(JSON.parse(stdout)); } catch { reject(new Error(unconfirmed)); }
    });
  });
}
