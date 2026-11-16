/** Own the one Electron controller process before any window or backend is started. */
export function createInstanceLifecycle({ app, parseConnection, createWindow, focusWindow }) {
  let window = null;
  let connection = null;
  let opening = null;
  let replacing = false;
  let ready = false;
  let focusWhenReady = false;

  function focus() {
    if (!ready || !window || window.isDestroyed?.()) {
      focusWhenReady = true;
      return;
    }
    if (window.isMinimized?.()) window.restore();
    focusWindow(window);
  }

  function setReady() {
    ready = true;
  }

  function trackWindow(created) {
    window = created;
    created.on?.("closed", () => { if (window === created) window = null; });
    if (focusWhenReady) {
      focusWhenReady = false;
      focus();
    }
    return created;
  }

  function acquire(args) {
    if (!app.requestSingleInstanceLock()) {
      app.quit();
      return false;
    }

    // Register before readiness so a rapid second launch is not lost. A duplicate launch only
    // focuses the existing controller; it cannot replace its authenticated connection.
    app.on("second-instance", () => {
      focus();
    });

    try {
      connection = parseConnection(args);
    } catch (error) {
      app.quit();
      throw error;
    }
    return true;
  }

  async function open() {
    if (!connection) throw new Error("A verified local Heinzelmännchen connection is required.");
    if (!ready) {
      focusWhenReady = true;
      return null;
    }
    if (window && !window.isDestroyed?.()) {
      focus();
      return window;
    }
    if (!opening) {
      opening = Promise.resolve(createWindow(connection)).then((created) => {
        return trackWindow(created);
      }).finally(() => { opening = null; });
    }
    return opening;
  }

  function currentConnection() {
    if (!connection) throw new Error("No authenticated local connection is active.");
    return connection;
  }

  function setConnection(value) {
    if (connection) throw new Error("The authenticated local connection is already set.");
    if (!value || typeof value.origin !== "string" || typeof value.token !== "string")
      throw new Error("A verified local Heinzelmännchen connection is required.");
    connection = value;
  }

  async function replaceConnection(value) {
    if (!value || typeof value.origin !== "string" || typeof value.token !== "string")
      throw new Error("An authenticated workspace is required.");
    if (replacing) throw new Error("A workspace is already opening.");
    replacing = true;
    try {
      // The first local window may still be loading when the person selects a workspace.
      // Let that attempt settle, then replace it (or open the selection if it failed).
      if (opening) {
        try { await opening; } catch { /* The selected workspace can still open. */ }
      }
      const previous = window;
      const previousConnection = connection;
      connection = value;
      opening = Promise.resolve().then(() => createWindow(value));
      try {
        const created = await opening;
        if (previous && !previous.isDestroyed()) previous.destroy();
        return trackWindow(created);
      } catch (error) {
        connection = previousConnection;
        throw error;
      } finally { opening = null; }
    } finally { replacing = false; }
  }

  return Object.freeze({ acquire, open, currentConnection, focus, setReady, setConnection, replaceConnection });
}
