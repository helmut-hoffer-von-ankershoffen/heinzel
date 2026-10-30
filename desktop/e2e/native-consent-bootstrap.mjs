// Test-only production entrypoint, isolated before Electron obtains its profile or singleton lock.
import { app, dialog } from 'electron';
import { join } from 'node:path';
const root = process.env.HZ_NATIVE_TEST_ROOT;
if (!root) throw new Error('Missing isolated test root');
app.setPath('userData', join(root, 'profile'));
app.setPath('sessionData', join(root, 'session'));
app.setPath('logs', join(root, 'logs'));
globalThis.consentPrompts = [];
dialog.showMessageBox = async (_owner, options) => {
  globalThis.consentPrompts.push(options.title);
  return { response: 0, checkboxChecked: false };
};
dialog.showErrorBox = () => {};
await import('../src/main.js');
