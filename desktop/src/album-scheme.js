const registered = new WeakSet();

// Electron accepts this registration once, before its ready event.
export function registerAlbumScheme(protocol) {
  if (registered.has(protocol)) return;
  protocol.registerSchemesAsPrivileged([{
    scheme: "heinzel-album",
    privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true },
  }]);
  registered.add(protocol);
}
