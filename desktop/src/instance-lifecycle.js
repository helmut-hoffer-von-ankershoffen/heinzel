/** Own the one Electron controller process before any window or backend is started. */
export function createInstanceLifecycle({ app, parseConnection, createWindow, focusWindow }) {
  let window = null;
  let connection = null;
  let opening = null;
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
        window = created;
        window.on?.("closed", () => { window = null; });
        if (focusWhenReady) {
          focusWhenReady = false;
          focus();
        }
        return window;
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

  return Object.freeze({ acquire, open, currentConnection, focus, setReady, setConnection });
}
