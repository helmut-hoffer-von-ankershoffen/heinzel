import { isOwnedUiNavigation } from "./ui-navigation.js";

const CONSENT = Object.freeze({
  type: "question", title: "Share workspace audio?",
  message: "Allow the visualizer to analyse audio playing in this Heinzelmännchen workspace?",
  detail: "Only this workspace is shared, including its embedded player. No microphone or other app is captured. Audio stays in this window. Stop sharing in the visualizer to end capture.",
  buttons: Object.freeze(["Don't allow", "Share workspace audio"]), defaultId: 0, cancelId: 0, noLink: true,
});
const SYSTEM_CONSENT = Object.freeze({
  type: "question", title: "Share system audio?",
  message: "Allow the visualizer to analyse all sound this computer plays?",
  detail: "This includes other apps. No picture and no microphone are captured. Audio stays in this window and is never recorded. Stop sharing in the visualizer to end capture.",
  buttons: Object.freeze(["Don't allow", "Share system audio"]), defaultId: 0, cancelId: 0, noLink: true,
});
// An armed source choice is consumed by the next display request of the same window, or expires.
const ARMED_MS = 10_000;

/**
 * Whether Electron can loop back system audio without a picker: Windows, and macOS 14.2 (Darwin 23.2) or later,
 * where Chromium captures through a Core Audio tap (NSAudioCaptureUsageDescription). Elsewhere it is not offered.
 */
export function systemAudioAvailable(platform, release) {
  if (platform === "win32") return true;
  if (platform !== "darwin") return false;
  const [major, minor] = String(release).split(".").map(Number);
  return Number.isInteger(major) && (major > 23 || (major === 23 && minor >= 2));
}

/** One JSON line per event (ADR 0138 names and fields: phase, outcome ok|error|skipped), never tokens or URLs. */
const stderrLog = (event, fields) => {
  try { process.stderr.write(`${JSON.stringify({ event, ...fields })}\n`); } catch { /* logging never breaks capture */ }
};

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
export function createWorkspaceAudio({ windows, fromWebContents, fromFrame, connection, prompt,
  system = false, log = stderrLog, now = Date.now }) {
  let pending = null;
  let armed = null;
  let generation = 0;
  const revoke = () => { generation++; armed = null; pending?.finish(false); };
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
  /** IPC: the owned, focused main document names the source of its next capture; "system" only where available. */
  const choose = (event, source) => {
    try {
      const frame = event?.senderFrame;
      const contents = event?.sender;
      if (source !== "workspace" && (source !== "system" || !system) || !frame || contents?.mainFrame !== frame) return false;
      const owned = snapshot(contents, frame, new URL(frame.url).origin);
      if (!owned) return false;
      armed = { contents, source, at: now() };
      log("desktop.audio.source", { operation: "audio_capture", step: "source", source, outcome: "ok" });
      return true;
    } catch { return false; }
  };
  const available = () => system;
  const display = (request, callback) => {
    let original;
    try {
      original = request?.userGesture === true && request.audioRequested === true && request.videoRequested === true &&
        snapshot(fromFrame(request.frame), request.frame, request.securityOrigin);
    } catch { original = null; }
    const choice = armed;
    armed = null;
    const source = original && choice?.contents === original.contents && now() - choice.at <= ARMED_MS && system ?
      choice.source : "workspace";
    if (!original || pending) {
      log("desktop.audio.capture", { operation: "audio_capture", step: "request", phase: "end", source,
        outcome: "skipped", reason: pending ? "pending" : "unowned" });
      denyDisplay(callback); return;
    }
    let completed = false;
    const epoch = generation;
    const started = now();
    log("desktop.audio.capture", { operation: "audio_capture", step: "consent", phase: "start", source });
    const finish = (allowed) => {
      if (completed) return;
      completed = true;
      log("desktop.audio.capture", { operation: "audio_capture", step: "consent", phase: "end", source,
        outcome: allowed ? "ok" : "skipped", ...(allowed ? {} : { reason: "declined" }), duration_ms: now() - started });
      // Video is always the fixed requesting main frame (never a screen or picker). Audio is that frame's own
      // sound, or the system loopback the person chose and consented to; neither is ever a reusable grant.
      if (allowed && source === "system") callback({ video: request.frame, audio: "loopback" });
      else if (allowed) callback({ video: request.frame, audio: request.frame, enableLocalEcho: true });
      else denyDisplay(callback);
    };
    pending = { finish };
    Promise.resolve().then(() => {
      if (completed || generation !== epoch || !same(request, original)) return null;
      return prompt(original.window, source === "system" ? SYSTEM_CONSENT : CONSENT);
    }).then(
      (result) => finish(result?.response === 1 && generation === epoch && Boolean(same(request, original))),
      () => finish(false),
    ).finally(() => { if (pending?.finish === finish) pending = null; });
  };
  const navigation = (details) => { if (details?.isMainFrame === true) revoke(); };
  return Object.freeze({ check, request, display, revoke, navigation, choose, available });
}
