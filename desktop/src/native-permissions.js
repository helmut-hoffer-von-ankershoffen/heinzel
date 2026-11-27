import { isOwnedUiNavigation } from "./ui-navigation.js";

const PROMPTS = Object.freeze({
  notifications: Object.freeze({
    type: "question", title: "Allow notifications?",
    message: "Allow this Heinzelmännchen window to request system notifications?",
    detail: "This choice lasts until this window navigates or closes. Your operating system may separately block notifications; this does not mean a notification was shown.",
    buttons: Object.freeze(["Don't allow", "Allow"]), defaultId: 0, cancelId: 0, noLink: true,
  }),
  microphone: Object.freeze({
    type: "question", title: "Allow microphone?",
    message: "Allow this Heinzelmännchen window to request microphone access?",
    detail: "This choice lasts until this window navigates or closes. Your operating system may separately ask for or deny microphone access.",
    buttons: Object.freeze(["Don't allow", "Allow"]), defaultId: 0, cancelId: 0, noLink: true,
  }),
});

/** Document-scoped native consent; never an OS permission or delivery receipt. */
export function createNativePermissions({ windows, fromWebContents, connection, prompt }) {
  let generation = 0;
  let pending = null;
  const grants = new Map();
  let notificationChoice = null;

  function revoke() {
    generation++;
    grants.clear(); notificationChoice = null;
    if (pending) pending.finish(false);
  }

  function snapshot(contents, details) {
    try {
      const window = contents && !contents.isDestroyed() ? fromWebContents(contents) : null;
      const frame = contents?.mainFrame;
      const selected = connection();
      if (!window || !windows.has(window) || window.isDestroyed() || !frame || frame.isDestroyed() || frame.detached ||
          details?.isMainFrame !== true || typeof details.requestingUrl !== "string" ||
          details.requestingUrl !== frame.url || !isOwnedUiNavigation(frame.url, selected) ||
          (details.securityOrigin !== undefined && details.securityOrigin !== selected.origin &&
            details.securityOrigin !== `${selected.origin}/`)) return null;
      return { window, frame, processId: frame.processId, frameToken: frame.frameToken, url: frame.url, origin: selected.origin, token: selected.token };
    } catch {
      return null;
    }
  }

  function same(contents, details, original) {
    const current = snapshot(contents, details);
    return current && current.window === original.window && current.frame === original.frame &&
      current.processId === original.processId && current.frameToken === original.frameToken &&
      current.url === original.url && current.origin === original.origin && current.token === original.token;
  }

  function request(contents, permission, callback, details) {
    const kind = permission === "notifications" ? "notifications" :
      permission === "media" && Array.isArray(details?.mediaTypes) && details.mediaTypes.length === 1 &&
      details.mediaTypes[0] === "audio" ? "microphone" : null;
    const original = kind && snapshot(contents, details);
    if (!original || pending) { callback(false); return; }
    const grant = grants.get(kind);
    if (grant && same(contents, details, grant)) { callback(true); return; }
    let completed = false;
    const epoch = generation;
    const finish = (allowed) => {
      if (completed) return;
      completed = true;
      if (allowed === true) grants.set(kind, original);
      callback(allowed === true);
    };
    pending = { finish };
    Promise.resolve().then(() => {
      if (completed || generation !== epoch || !same(contents, details, original)) return null;
      return prompt(original.window, PROMPTS[kind]);
    }).then(
      (result) => {
        const current = generation === epoch && Boolean(same(contents, details, original));
        if (kind === "notifications" && result && current) notificationChoice = result.response === 1;
        finish(result?.response === 1 && current);
      },
      () => finish(false),
    ).finally(() => { if (pending?.finish === finish) pending = null; });
  }

  // Electron may ask for a permission status after the request. Require the same proven document and kind.
  function check(contents, permission, requestingOrigin, details) {
    const kind = permission === "notifications" ? "notifications" :
      permission === "media" && details?.mediaType === "audio" ? "microphone" : null;
    const grant = kind && grants.get(kind);
    if (!grant || (requestingOrigin !== grant.origin && requestingOrigin !== `${grant.origin}/`)) return false;
    return Boolean(same(contents, details, grant));
  }

  function navigation(details) {
    if (details?.isMainFrame === true) revoke();
  }

  const notificationStatus = () => notificationChoice === null ? "Ask for workspace access"
    : notificationChoice ? "Allowed in workspace · system permission unknown" : "Denied in workspace";
  return Object.freeze({ request, check, revoke, navigation, notificationStatus });
}
