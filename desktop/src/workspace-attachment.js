import { randomUUID } from "node:crypto";

export const WORKSPACE_WORLD = 1001;

// Runs in the selected document's isolated JS world. Only public facts cross the renderer boundary.
async function readSession(origin, recoveryTicket) {
  const credential = () => sessionStorage.getItem("hz.token") ?? localStorage.getItem("hz.device");
  if (location.origin !== origin) return { changed: true };
  const sent = credential();
  if (!sent) return { status: 401 };
  const paired = localStorage.getItem("hz.device");
  let usingPair = false;
  const authorityCurrent = () => credential() === sent && (!usingPair || localStorage.getItem("hz.device") === paired);
  if (recoveryTicket) {
    // The isolated world belongs to this document. Keep at most one pending acknowledgement.
    globalThis.finishWorkspaceRecovery = (ticket) => {
      if (ticket !== recoveryTicket) return;
      delete globalThis.finishWorkspaceRecovery;
      if (location.origin === origin && authorityCurrent()) window.dispatchEvent(new Event("hz:workspace-restored"));
    };
  }
  const signal = AbortSignal.timeout(4500);
  const read = (token) => fetch(origin + "/api/lifecycle", { mode: "same-origin", headers: { Authorization: "Bearer " + token, Accept: "application/json" },
    redirect: "error", cache: "no-store", credentials: "omit", signal });
  try {
    let response = await read(sent);
    if (!authorityCurrent()) return { changed: true };
    if (response.status === 401 && paired && paired !== sent) {
      // An idle document can recover with its paired device. The page owns adoption of that credential.
      usingPair = true;
      if (!authorityCurrent()) return { changed: true };
      response = await read(paired);
      if (!authorityCurrent()) return { changed: true };
    }
    if (!response.ok) return { status: response.status };
    const value = await response.json().catch(() => null);
    if (!authorityCurrent()) return { changed: true };
    if (typeof value?.instance !== "string" || !value.instance.trim() || typeof value.version !== "string" || !value.version.trim())
      return { code: "INVALID_LIFECYCLE" };
    return { instance: value.instance, version: value.version,
      authoritySource: usingPair || sessionStorage.getItem("hz.token") === null ? "device" : "session" };
  } catch {
    return { code: navigator.onLine === false ? "ENETUNREACH" : "UNAVAILABLE" };
  }
}

/** Navigation, pause and authority changes invalidate a pending document result. No rejected credential is erased here. */
export async function probeWorkspaceRenderer(window, connection, signal, recovering = false) {
  const contents = window?.webContents;
  if (!contents || contents.isDestroyed()) throw new Error("Workspace window unavailable.");
  const frame = contents.mainFrame;
  const changed = () => Object.assign(new Error("Workspace session changed."), { code: "SESSION_CHANGED" });
  if (signal?.aborted || new URL(frame.url).origin !== connection.origin) throw changed();
  const ticket = recovering ? randomUUID() : null;
  const code = `(${readSession})(${JSON.stringify(connection.origin)},${JSON.stringify(ticket)})`;
  let timer, abort;
  try {
    const result = await Promise.race([
      contents.executeJavaScriptInIsolatedWorld(WORKSPACE_WORLD, [{ code }]),
      new Promise((_, reject) => {
        abort = () => reject(changed());
        signal?.addEventListener("abort", abort, { once: true });
        timer = setTimeout(() => reject(new Error("Workspace probe timed out.")), 5000);
      }),
    ]);
    if (signal?.aborted || contents.isDestroyed() || contents.mainFrame !== frame || result?.changed) throw changed();
    if (!result?.instance) throw Object.assign(new Error("Workspace unavailable."), { status: result?.status, code: result?.code });
    if (ticket) result.notifyRestored = () => {
      if (signal?.aborted || contents.isDestroyed() || contents.mainFrame !== frame) return;
      void contents.executeJavaScriptInIsolatedWorld(WORKSPACE_WORLD, [{
        code: `globalThis.finishWorkspaceRecovery?.(${JSON.stringify(ticket)})`,
      }]).catch(() => {});
    };
    return result;
  } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
}

export async function verifyWorkspaceRenderer(window, connection) {
  const attached = await window.webContents.executeJavaScriptInIsolatedWorld(WORKSPACE_WORLD, [{ code: `(async () => {
    const until = performance.now() + 9000;
    while (!document.querySelector('[data-testid="app-shell"]')) {
      if (performance.now() >= until) return false;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    return true;
  })()` }]);
  if (!attached) throw new Error("The workspace could not open.");
  await probeWorkspaceRenderer(window, connection);
}
