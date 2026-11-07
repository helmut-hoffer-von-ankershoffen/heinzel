// The product window's options per platform (ADR 0078). On macOS the web draws one 44 px title bar row and the
// traffic lights sit inside it; Windows and Linux keep the native frame for now, so their insets stay 0.
export const TITLEBAR_HEIGHT = 44;

export function windowOptions(platform) {
  const base = { width: 1100, height: 760, show: false, title: "Heinzelmännchen" };
  if (platform !== "darwin") return base;
  // 12 px lights centred in the 44 px row; the three lights and their gaps end at x = 78, the web keeps 80 free.
  return { ...base, titleBarStyle: "hiddenInset", trafficLightPosition: { x: 14, y: 16 } };
}
