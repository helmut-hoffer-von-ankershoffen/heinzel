/** Own the one Electron controller process before any window or backend is started. */
export function createInstanceLifecycle({ app, parseConnection, createWindow, focusWindow }) {
  let window = null;
  let connection = null;
  let opening = null;
  let revision = 0;
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
      const ticket = revision;
      opening = Promise.resolve(createWindow(connection)).then((created) => {
        if (ticket !== revision) { created.destroy(); return null; }
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

  function disconnect() {
    revision++;
    connection = null;
    if (window && !window.isDestroyed()) window.destroy();
    window = null;
  }

  async function replaceConnection(value, valid = () => true) {
    if (!value || typeof value.origin !== "string" || typeof value.token !== "string")
      throw new Error("An authenticated workspace is required.");
    // Let startup settle, retaining the previous document until its successor has authenticated and rendered.
    if (opening) { try { await opening; } catch { /* The new selection can still open. */ } }
    if (!valid()) throw new Error("Selection cancelled.");
    const ticket = ++revision;
    const cancelled = () => ticket !== revision || !valid();
    const created = await createWindow(value, cancelled);
    if (cancelled()) { created.destroy(); throw new Error("Selection cancelled."); }
    const previous = window;
    connection = value;
    trackWindow(created);
    if (previous && !previous.isDestroyed()) previous.destroy();
    return created;
  }

  return Object.freeze({ acquire, open, currentConnection, currentWindow: () => window,
    focus, setReady, setConnection, replaceConnection, disconnect });
}
