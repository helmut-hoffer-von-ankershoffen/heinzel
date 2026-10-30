import { app, BrowserWindow, session } from 'electron';
import { createNativePermissions } from '../src/native-permissions.js';

app.setPath('userData', process.env.HZ_TEST_PROFILE);
app.setPath('sessionData', process.env.HZ_TEST_PROFILE);
app.whenReady().then(async () => {
const window = new BrowserWindow({ show: false, webPreferences: { contextIsolation: true, sandbox: true } });
const windows = new WeakSet([window]);
const connection = { origin: process.env.HZ_TEST_ORIGIN, token: 'synthetic_permission_fixture_token' };
globalThis.permissionEvents = [];
const permissions = createNativePermissions({ windows, fromWebContents: BrowserWindow.fromWebContents,
  connection: () => connection, prompt: async (_window, options) => {
    globalThis.permissionEvents.push({ kind: 'prompt', title: options.title });
    return { response: globalThis.syntheticAccept ? 1 : 0 };
  } });
globalThis.revokeSynthetic = () => permissions.revoke();
globalThis.authorizeSynthetic = () => new Promise((resolve) => {
  const frame = window.webContents.mainFrame;
  globalThis.syntheticAccept = true;
  permissions.request(window.webContents, 'notifications', (allowed) => {
    globalThis.syntheticAccept = false; resolve(allowed);
  }, { isMainFrame: true, requestingUrl: frame.url });
});
session.defaultSession.setPermissionCheckHandler((contents, permission, origin, details) => {
  globalThis.permissionEvents.push({ kind: 'check', permission, origin, details });
  return permissions.check(contents, permission, origin, details);
});
session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) => {
  globalThis.permissionEvents.push({ kind: 'request', permission, details });
  permissions.request(contents, permission, callback, details);
});
window.on('closed', () => { permissions.revoke(); app.quit(); });
await window.loadURL(connection.origin + '/');
}).catch((error) => { console.error(error); app.quit(); });
