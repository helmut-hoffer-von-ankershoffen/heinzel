import { isOwnedUiNavigation } from "./ui-navigation.js";

const CONSENT = Object.freeze({
  type: "question", title: "Share workspace audio?",
  message: "Allow the visualizer to analyse audio playing in this Heinzelmännchen workspace?",
  detail: "Only this workspace is shared, including its embedded player. No microphone or other app is captured. Audio stays in this window. Stop sharing in the visualizer to end capture.",
  buttons: Object.freeze(["Don't allow", "Share workspace audio"]), defaultId: 0, cancelId: 0, noLink: true,
});

function denyDisplay(callback) {
  try { callback({}); }
  catch (error) {
    // Electron 45 rejects the renderer's request, then synchronously throws for an empty video selection.
    // This exact denial has already completed: never retry the callback or hide any other callback failure.
    if (!(error instanceof TypeError) || error.message !== "Video was requested, but no video stream was provided")
      throw error;
  }
}

/** One explicitly consented capture of the requesting workspace, never a reusable grant. */
export function createWorkspaceAudio({ windows, fromWebContents, fromFrame, connection, prompt }) {
  let pending = null;
  let generation = 0;
  const revoke = () => { generation++; pending?.finish(false); };
  const snapshot = (contents, frame, origin) => {
    try {
      const window = contents && !contents.isDestroyed() ? fromWebContents(contents) : null;
      const main = contents?.mainFrame;
      const selected = connection();
      if (!window || !windows.has(window) || window.webContents !== contents || window.isDestroyed() || !window.isFocused() || !window.isVisible() ||
          !frame || !main || frame.isDestroyed() || main.isDestroyed() || frame.detached || main.detached ||
          frame.processId !== main.processId || frame.frameToken !== main.frameToken ||
          !isOwnedUiNavigation(frame.url, selected) || frame.url !== main.url ||
          (origin !== selected.origin && origin !== `${selected.origin}/`)) return null;
      return { contents, window, frame, processId: frame.processId, frameToken: frame.frameToken,
        url: frame.url, origin: selected.origin, token: selected.token };
    } catch { return null; }
  };
  const same = (request, original) => {
    try {
      const current = snapshot(fromFrame(request.frame), request.frame, request.securityOrigin);
      return current && current.contents === original.contents && current.window === original.window &&
        current.processId === original.processId && current.frameToken === original.frameToken &&
        current.url === original.url && current.origin === original.origin && current.token === original.token;
    } catch { return false; }
  };
  // These checks admit only entry into the separate, always-prompted display handler.
  const check = (contents, permission, origin, details) => {
    try {
      if (permission !== "display-capture" || details?.isMainFrame !== true) return false;
      const main = contents?.mainFrame;
      return Boolean(main && details.requestingUrl === main.url && snapshot(contents, main, origin));
    } catch { return false; }
  };
  const request = (contents, permission, callback, details) => {
    callback(check(contents, permission, details?.securityOrigin ?? connection()?.origin, details));
  };
  const display = (request, callback) => {
    let original;
    try {
      original = request?.userGesture === true && request.audioRequested === true && request.videoRequested === true &&
        snapshot(fromFrame(request.frame), request.frame, request.securityOrigin);
    } catch { original = null; }
    if (!original || pending) { denyDisplay(callback); return; }
    let completed = false;
    const epoch = generation;
    const finish = (allowed) => {
      if (completed) return;
      completed = true;
      // Frame is the fixed requesting main frame. No system-loopback or selectable source IDs exist here.
      if (allowed) callback({ video: request.frame, audio: request.frame, enableLocalEcho: true });
      else denyDisplay(callback);
    };
    pending = { finish };
    Promise.resolve().then(() => {
      if (completed || generation !== epoch || !same(request, original)) return null;
      return prompt(original.window, CONSENT);
    }).then(
      (result) => finish(result?.response === 1 && generation === epoch && Boolean(same(request, original))),
      () => finish(false),
    ).finally(() => { if (pending?.finish === finish) pending = null; });
  };
  const navigation = (details) => { if (details?.isMainFrame === true) revoke(); };
  return Object.freeze({ check, request, display, revoke, navigation });
}
