// The workspace chooser keeps the committed window alive until its successor is ready.
import path from "node:path";
import { BrowserWindow, Notification, app, net, powerMonitor, safeStorage } from "electron";
import { lifecycle } from "./connection.js";
import { acquireDesktopGate, createOwnedCli, developmentCli, installedCli } from "./owned-cli.js";
import { stopWithReport } from "./shutdown.js";
import { createWorkspaceController } from "./workspace-controller.js";
import { createWorkspaceDirectory, workspaceName } from "./workspace-directory.js";
import { createWorkspaceMonitor } from "./workspace-monitor.js";
import { probeWorkspaceRenderer } from "./workspace-attachment.js";
import { readWorkspaceFacts, watchWorkspaceAuthority } from "./workspace-facts.js";
import { keepDesktopGate, showConnection } from "./main-picker.js";
import { applicationInfo } from "./main-services.js";
import { watchLocalService } from "./main-service-watch.js";
import { ctx, windows } from "./main-state.js";

export function createController() {
  const directory = createWorkspaceDirectory({ file: path.join(app.getPath("userData"), "workspaces.json"), safeStorage });
  let active = null, facts = {}, localVersion = null, detailsRevision = 0, checking = null;
  let internet = "Checking", localAttach = null, revision = 0;
  const name = (connection) => connection.remote ? workspaceName(connection.origin) : "This Mac";
  const publish = () => {
    if (ctx.picker && !ctx.picker.isDestroyed()) ctx.picker.webContents.send("hz:workspace-state", ctx.pickerState());
  };
  const clearDetails = () => {
    detailsRevision++; checking?.abort();
    facts = { version: active ? ctx.windowVersions.get(ctx.instance.currentWindow()) : null };
  };
  ctx.pickerState = () => {
    const state = ctx.workspaceController.state();
    const recovery = ctx.monitor.state();
    return { ...state, revision: ++revision, recovery, localOrigin: ctx.localConnection?.origin, localVersion,
      application: { ...applicationInfo(), ...(facts.clientAddress ? { ipAddress: facts.clientAddress } : {}) },
      workspace: active && !recovery && !ctx.rendererFailed ? { ...facts, name: name(active), origin: active.origin } : null,
      localAvailable: Boolean(ctx.ownedCli.isRunning()), checks: { internet,
        notifications: !Notification.isSupported() ? "Unavailable" : active?.remote ? "Restricted in remote workspaces"
          : ctx.permissions?.notificationStatus?.() ?? "Supported · system permission unknown", icloud: "Unavailable" } };
  };
  ctx.refreshPicker = () => {
    clearDetails();
    const revision = detailsRevision;
    publish();
    checking = new AbortController();
    const signal = AbortSignal.any([checking.signal, AbortSignal.timeout(4500)]);
    void net.fetch("https://heinzel.team/", { method: "HEAD", redirect: "error", credentials: "omit", signal })
      .then(() => { if (revision === detailsRevision) internet = "Online"; },
        () => { if (revision === detailsRevision) internet = net.isOnline() ? "Unreachable" : "Offline"; })
      .finally(() => { if (revision === detailsRevision) publish(); });
    const window = ctx.instance.currentWindow();
    if ((!active || ctx.rendererFailed) && ctx.workspaceController.state().canRetry && !ctx.workspaceController.state().busy && !ctx.monitor.state()) {
      try { void ctx.workspaceController.inspect(ctx.instance.currentConnection()); } catch { /* No selected session. */ }
    }
    if (!active || !window || ctx.monitor.state() || ctx.rendererFailed) return;
    const target = active;
    void readWorkspaceFacts(target, window.webContents, signal).then((value) => {
      if (revision !== detailsRevision || active !== target || ctx.monitor.state()) return;
      facts = { ...value, version: value.version ?? facts.version };
      if (!target.remote) localVersion = facts.version;
      publish();
    }).catch(() => {});
  };
  let stopWatchingInformation, lastProbeAuthority;
  ctx.showInformation = (show) => {
    stopWatchingInformation?.();
    const picker = ctx.picker, contents = ctx.instance.currentWindow()?.webContents;
    if (!show || !picker || !active || !contents || ctx.monitor.state()) return;
    clearDetails(); publish();
    const controller = new AbortController();
    const stop = () => {
      controller.abort();
      picker.removeListener("hide", stop);
      picker.removeListener("closed", stop);
      stopWatchingInformation = null;
      clearDetails();
    };
    stopWatchingInformation = stop;
    picker.once("hide", stop);
    picker.once("closed", stop);
    watchWorkspaceAuthority(active, contents, controller.signal, ctx.refreshPicker);
  };
  ctx.selected = (target, observed) => {
    stopWatchingInformation?.();
    lastProbeAuthority = null;
    detailsRevision++; checking?.abort(); active = target; facts = { version: target ? observed?.version ?? ctx.windowVersions.get(ctx.instance.currentWindow()) : null };
    ctx.rendererFailed = false; ctx.editing = false;
    const window = target && ctx.instance.currentWindow();
    const capabilities = window && ctx.windowCapabilities.get(window);
    ctx.permissions = capabilities?.permissions; ctx.workspaceAudio = capabilities?.audio;
    if (capabilities) windows.add(window);
    if (window) {
      window.webContents.on("did-start-navigation", (event) => {
        if (event.isMainFrame && window === ctx.instance.currentWindow()) { clearDetails(); publish(); }
      });
    }
    if (target && !target.remote && facts.version) localVersion = facts.version;
    ctx.monitor.select(target);
    publish();
  };
  ctx.probeLocal = (connection) => active === connection && ctx.instance.currentWindow()
    ? probeWorkspaceRenderer(ctx.instance.currentWindow(), connection) : lifecycle(connection);
  ctx.monitor = createWorkspaceMonitor({
    probe: (connection, signal) => probeWorkspaceRenderer(ctx.instance.currentWindow(), connection, signal, Boolean(ctx.monitor.state())),
    observed: (connection, value) => {
      if (value?.authoritySource && lastProbeAuthority !== value.authoritySource) {
        lastProbeAuthority = value.authoritySource;
        if (stopWatchingInformation) ctx.refreshPicker();
      }
      if (value?.version) {
        facts.version = value.version;
        const window = ctx.instance.currentWindow();
        if (window) ctx.windowVersions.set(window, value.version);
        if (!connection.remote) localVersion = value.version;
      }
    },
    changed: publish,
    lost: () => { clearDetails(); showConnection(true); },
    recovered: (connection, result) => {
      if (ctx.stopping || ctx.quitting || ctx.rendererFailed || !ctx.instance.currentWindow() ||
          active !== connection || ctx.instance.currentConnection() !== connection) return;
      if (!connection.remote) ctx.browserBridge.start();
      ctx.picker?.hide(); ctx.instance.focus();
      result.notifyRestored?.();
    },
  });
  let suspended = false;
  function activeWindow(value) {
    if (ctx.foreground === value) return;
    ctx.foreground = value; ctx.probeEpoch = (ctx.probeEpoch ?? 0) + 1;
    ctx.monitor.awake(value);
  }
  powerMonitor.on("suspend", () => { suspended = true; activeWindow(false); });
  powerMonitor.on("resume", () => { suspended = false; activeWindow(true); });
  app.on("browser-window-blur", () => setImmediate(() => {
    if (!BrowserWindow.getFocusedWindow()) activeWindow(false);
  }));
  app.on("browser-window-focus", () => { if (!suspended) activeWindow(true); });
  app.on("activate", () => { if (!suspended) activeWindow(true); });
  app.on("browser-window-focus", () => { if (ctx.picker?.isFocused()) ctx.refreshPicker(); });
  app.on("before-quit", () => { ctx.monitor.select(null); detailsRevision++; checking?.abort(); });

  async function attachLocal() {
    if (ctx.stopping || ctx.quitting) throw new Error("Desktop is stopping.");
    if (ctx.localConnection && ctx.ownedCli.isRunning()) return ctx.localConnection;
    await stopWithReport(ctx.ownedCli, () => {});
    if (ctx.stopping || ctx.quitting) throw new Error("Desktop is stopping.");
    ctx.ownedCli = createOwnedCli({ executable: app.isPackaged ? installedCli : () => developmentCli(),
      bridgeToken: ctx.bridgeToken, probe: (connection) => ctx.probeLocal(connection), probeContext: () => ctx.foreground === false ? null : ctx.probeEpoch ?? 0, env: { HZ_CALLER: "desktop-app" } });
    watchLocalService();
    if (ctx.managedMac && !ctx.desktopGate) keepDesktopGate(await acquireDesktopGate());
    if (ctx.stopping || ctx.quitting) throw new Error("Desktop is stopping.");
    ctx.localConnection = await ctx.ownedCli.start();
    ctx.serviceStarted = true;
    return ctx.localConnection;
  }
  ctx.workspaceController = createWorkspaceController({
    directory, current: () => active?.origin ?? null, changed: publish, verify: lifecycle,
    selected: ctx.selected, hold: (value) => ctx.monitor.hold(value || ctx.editing === true),
    local: () => localAttach ??= attachLocal().finally(() => { localAttach = null; }),
    replace: async (target, valid) => {
      if (ctx.stopping || ctx.quitting) throw new Error("Desktop is stopping.");
      ctx.switching = (ctx.switching || 0) + 1;
      try {
        ctx.instance.setReady();
        await ctx.instance.replaceConnection(target, valid);
        ctx.browserBridge.stop();
        if (!target.remote) ctx.browserBridge.start();
      } finally { ctx.switching--; }
    },
  });
}
