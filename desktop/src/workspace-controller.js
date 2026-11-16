import { parseSharedWorkspace, parseWorkspaceLink } from "./workspace-directory.js";

const inputError = (message) => Object.assign(new Error(message), { userMessage: message });

export function workspaceProblem(error) {
  const code = error?.cause?.code ?? error?.code ?? "";
  if (/CERT|TLS|SSL/.test(code)) return "Secure connection unavailable. Ask the host to check the workspace’s HTTPS certificate.";
  if (["ENETUNREACH", "EHOSTUNREACH"].includes(code)) return "You’re offline. Connect to your network, then try again.";
  if (error?.name === "TimeoutError") return "Still trying to connect. Check that the host is awake, then try again.";
  if (error?.status === 401) return "Sign-in required. Ask the host for a fresh private sign-in link.";
  if (error?.status === 403) return "This workspace denied access. Check your access to the host’s private network.";
  if (error?.status === 404 || error?.status === 410) return "No Heinzel workspace at this address. Ask the host to share it again.";
  return "Workspace out of reach. Check the host, network and HTTPS certificate, then try again.";
}

/** Serialize transitions; verification finishes before a working renderer is removed. */
export function createWorkspaceController({ directory, local, verify, replace, changed, current }) {
  let busy = false;
  let message = "";
  let problem = false;
  let retry = null;
  let needsSignIn = false;
  let sharedOrigin = null;
  const state = () => ({ busy, message, problem, needsSignIn, current: current(), recent: directory.list(), canRetry: retry !== null });
  async function connect(request, force = false) {
    if (busy) return state();
    const recovering = retry?.origin === current();
    busy = true;
    problem = false;
    needsSignIn = false;
    retry = null;
    message = request.kind === "local" ? "Starting the local workspace…" : "Checking workspace connection…";
    changed(state());
    try {
      let target;
      if (request.kind === "local") { sharedOrigin = null; target = await local(); }
      else if (request.kind === "recent") {
        sharedOrigin = null;
        target = request.origin === current() ? { origin: request.origin } : directory.connection(request.origin);
        if (!target) throw Object.assign(inputError("Sign-in required. Paste a fresh private sign-in link below."), { status: 401 });
      } else if (request.kind === "link") {
        try { target = parseWorkspaceLink(request.link); }
        catch (error) {
          try { sharedOrigin = parseSharedWorkspace(request.link); }
          catch {
            try { sharedOrigin = parseWorkspaceLink(request.link, { requireToken: false }).origin; }
            catch { throw inputError(error.message); }
          }
          throw Object.assign(inputError(`Workspace ${sharedOrigin}. Paste its private sign-in link to continue.`), { status: 401 });
        }
        if (sharedOrigin && target.origin !== sharedOrigin)
          throw Object.assign(inputError(`Use a sign-in link for ${sharedOrigin}, or enter another shared workspace link.`), { status: 401 });
        sharedOrigin = null;
      } else throw inputError("Choose a workspace to continue.");
      if (!force && !recovering && request.kind !== "link" && target.origin === current()) {
        message = "Connected";
      } else {
        retry = target;
        await verify(target);
        await replace(target);
        const saved = !target.remote || directory.remember(target);
        message = saved ? "Connected" : "Connected. Sign-in is saved for this session only.";
        retry = null;
      }
    } catch (error) {
      problem = true; needsSignIn = error?.status === 401;
      if (needsSignIn) retry = null;
      message = error?.userMessage ?? workspaceProblem(error);
    }
    finally { busy = false; changed(state()); }
    return state();
  }
  return {
    state,
    connect,
    failed(target, error) { retry = target; problem = true; message = workspaceProblem(error); changed(state()); },
    async inspect(target) {
      if (!target || busy) return state();
      busy = true; problem = false; needsSignIn = false; message = "Checking connection…"; changed(state());
      try {
        await verify(target);
        if (retry?.origin === target.origin) message = "Connection restored. Try again to reopen the workspace.";
        else { retry = null; message = "Connected"; }
      }
      catch (error) { needsSignIn = error?.status === 401; retry = needsSignIn ? null : target; problem = true; message = workspaceProblem(error); }
      finally { busy = false; changed(state()); }
      return state();
    },
    async retry() {
      if (!retry || busy) return state();
      const target = retry;
      return connect(target.remote ? { kind: "link", link: `${target.origin}/#token=${target.token}` } : { kind: "local" }, true);
    },
    forget(origin) {
      if (!busy) {
        if (retry?.origin === origin) retry = null;
        problem = !directory.forget(origin);
        message = problem ? "Could not save this change. Try again." : "Workspace forgotten";
        changed(state());
      }
      return state();
    },
  };
}

/** An exact bundled main document, never a workspace renderer or an embedded frame. */
export function isPickerFrame(event, picker, pickerURL) {
  try {
    return Boolean(picker && !picker.isDestroyed() && event.sender === picker.webContents &&
      !event.sender.isDestroyed() && event.senderFrame === event.sender.mainFrame &&
      !event.senderFrame.isDestroyed() && !event.senderFrame.detached && event.senderFrame.url === pickerURL);
  } catch { return false; }
}
