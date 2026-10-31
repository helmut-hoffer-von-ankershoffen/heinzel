/** Node.js version used by repository CI to build the unsigned package. */
export const PINNED_NODE = "24.19.0";
const MINIMUM_MAJOR = Number(PINNED_NODE.split(".")[0]);

/**
 * Decide whether this Node.js may build the package.
 *
 * CI (or `HZ_DESKTOP_STRICT_NODE=1`) must run exactly the pinned version so release artifacts stay reproducible.
 * Local builds accept any newer Node.js at or above the pinned major and say so, since the packaged bytes come
 * from Electron and the locked dependencies, not from the Node.js that drives the packager.
 */
export function checkNode(version = process.versions.node, env = process.env) {
  const strict = env.CI === "true" || env.CI === "1" || env.HZ_DESKTOP_STRICT_NODE === "1";
  if (version === PINNED_NODE) return { ok: true, strict };
  if (strict) {
    return { ok: false, strict, message: `Electron packaging in CI requires Node.js ${PINNED_NODE}; found ${version}.` };
  }
  const major = Number(version.split(".")[0]);
  if (!Number.isInteger(major) || major < MINIMUM_MAJOR) {
    return { ok: false, strict, message: `Electron packaging requires Node.js ${MINIMUM_MAJOR} or newer (CI uses ${PINNED_NODE}); found ${version}.` };
  }
  return { ok: true, strict, warning: `Using Node.js ${version}; CI pins ${PINNED_NODE}. Set HZ_DESKTOP_STRICT_NODE=1 to require the pinned version.` };
}
