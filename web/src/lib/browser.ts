/** iPhone and iPad browsers, including iPadOS requesting desktop websites. */
export function isIOS(): boolean {
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

/** Desktop pages addressing loopback may also offer the server's native notification controls. */
export function isLocalServer(): boolean {
  return !isIOS() && ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
}
