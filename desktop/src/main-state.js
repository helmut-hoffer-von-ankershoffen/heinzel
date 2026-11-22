// State shared by the desktop's main-process modules: one object, so each concern lives in its own file.
export const windows = new WeakSet();
export const productWindows = new WeakSet();
export const activations = new WeakMap();
export const pickerURL = new URL("./workspace-picker.html", import.meta.url).href;
export const ctx = {
  quitting: false,
  stopping: false,
  permissions: undefined,
  desktopGate: undefined,
  gateReleasing: false,
  workspaceAudio: undefined,
  switching: false,
  startingProductWindow: false,
  picker: undefined,
  pickerLoading: false,
  workspaceController: undefined,
  localConnection: undefined,
  settingsStore: undefined,
  instance: undefined,
  miniBrowser: undefined,
  cookieImport: undefined,
  mediaPlayer: undefined,
  browserBridge: undefined,
  signalQuit: undefined,
  bridgeToken: undefined,
  managedMac: undefined,
  ownedCli: undefined,
  serviceStarted: false,
  unexpectedExitReported: false,
};
