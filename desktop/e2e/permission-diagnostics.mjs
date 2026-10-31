// Fixed-category observations on an already-failed click; never changes its actionability or result.
import { withTimeout } from './harness-cleanup.mjs';

export async function diagnosePermissionClick(page, instance, id, error) {
  try {
    const result = await withTimeout(page.evaluate(async (selector) => {
      const element = document.querySelector(selector);
      const rect = element?.getBoundingClientRect();
      const style = element ? getComputedStyle(element) : null;
      let frames = 0;
      let active = true;
      const frame = () => { if (active) { frames++; requestAnimationFrame(frame); } };
      requestAnimationFrame(frame);
      await new Promise((resolve) => setTimeout(resolve, 300));
      active = false;
      return { documentVisible: document.visibilityState === 'visible', exists: Boolean(element),
        enabled: element instanceof HTMLButtonElement && !element.disabled,
        display: style?.display !== 'none', visibility: style?.visibility === 'visible',
        hasSize: Boolean(rect && rect.width > 0 && rect.height > 0), framesAtLeastTwo: frames >= 2 };
    }, `#${id}`), 1500, 'Permission diagnostic timed out');
    const visible = await withTimeout(instance.evaluate(({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0];
      return Boolean(window && window.isVisible());
    }), 1500, 'Window diagnostic timed out');
    const fields = ['documentVisible', 'exists', 'enabled', 'display', 'visibility', 'hasSize', 'framesAtLeastTwo'];
    if (!result || typeof result !== 'object' || typeof visible !== 'boolean' || fields.some((key) => typeof result[key] !== 'boolean')) {
      throw new Error('Malformed permission diagnostic');
    }
    const safe = Object.fromEntries(fields.map((key) => [key, result[key]]));
    console.error('Permission click diagnostics:', JSON.stringify({ ...safe, windowVisible: visible }));
  } catch {
    console.error('Permission click diagnostics unavailable');
  }
  throw error;
}
