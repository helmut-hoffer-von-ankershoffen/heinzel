/** Keep the native display name separate from its HTTP product identity. */
export function configureDesktopIdentity(app) {
  app.setName("Heinzelmännchen");
  // Electron derives HTTP headers from the display name; a non-ASCII value breaks CacheStorage request serialization.
  const fallback = app.userAgentFallback.replaceAll("Heinzelmännchen", "Heinzelmaennchen");
  if (!/^[\x20-\x7e]+$/.test(fallback))
    throw new Error("The desktop HTTP identity could not be initialized.");
  app.userAgentFallback = fallback;
}

/** Refuse startup with actionable, fixed text when the HTTP identity is invalid. */
export function initializeDesktopIdentity(app, dialog) {
  try {
    configureDesktopIdentity(app);
    return true;
  } catch {
    try {
      dialog.showErrorBox("Heinzelmännchen could not open", "The app could not initialize its browser identity.");
    } catch {
      // Even if the native dialog is unavailable, never start with an invalid HTTP identity.
    }
    app.quit();
    return false;
  }
}
