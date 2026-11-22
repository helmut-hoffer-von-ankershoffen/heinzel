// Loss of the attached service: re-attach with a bounded backoff, then the connection screen.
import { reattach } from "./reattach.js";
import { showConnection } from "./main-picker.js";
import { ctx } from "./main-state.js";

export function watchLocalService() { void ctx.ownedCli.exited().then(async (result) => {
    // Only a loss of the attached service starts a re-attach. A failed attach (one of the re-attach attempts, or the
    // person's own) is reported by whoever started it, so it must not open the connection screen mid-backoff.
    if (!result?.serviceLost) return;
    if (ctx.serviceStarted && !ctx.stopping && !ctx.quitting) {
      ctx.localConnection = null;
      ctx.browserBridge.stop();
      let local = false;
      try { local = !ctx.instance.currentConnection().remote; } catch { /* No workspace yet. */ }
      if (!local) return;
      // The owned service restarted (a crash, a reboot of its job): attach again, with a fresh link and bridge token,
      // now and then with a bounded backoff. Only when every attempt failed does the connection screen offer Try Again.
      // The person switching to a remote workspace during the backoff ends it: that choice wins.
      const remote = () => { try { return Boolean(ctx.instance.currentConnection().remote); } catch { return false; } };
      const back = ctx.workspaceController && await reattach(async () => {
        const state = await ctx.workspaceController.connect({ kind: "local" }, true);
        return !state.problem;
      }, { cancelled: () => ctx.stopping || ctx.quitting || remote() });
      if (back || ctx.stopping || ctx.quitting || remote()) return;
      ctx.unexpectedExitReported = true;
      ctx.workspaceController?.failed(ctx.instance.currentConnection(), new Error("Local service stopped"));
      showConnection();
    }
  }); }
