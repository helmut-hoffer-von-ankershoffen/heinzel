// The owned CLI already confirms local loss with three consecutive failed checks.
import { ctx } from "./main-state.js";

export function watchLocalService() {
  const handle = ctx.ownedCli;
  void handle.exited().then((result) => {
    if (!result?.serviceLost || handle !== ctx.ownedCli || ctx.stopping || ctx.quitting) return;
    const local = ctx.localConnection;
    if (!local) return;
    let active;
    try { active = ctx.instance.currentConnection(); } catch { return; }
    if (active !== local) return;
    ctx.browserBridge.stop();
    ctx.monitor?.lostLocal(local);
  });
}
