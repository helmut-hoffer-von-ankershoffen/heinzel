// SDK ABI 1: the host owns this runtime. Plugins never bundle another copy.
const runtime = globalThis[Symbol.for("heinzel.plugin-sdk.1")].ReactDOM;
export default runtime;
export const createPortal = runtime.createPortal;
export const flushSync = runtime.flushSync;
export const preconnect = runtime.preconnect;
export const prefetchDNS = runtime.prefetchDNS;
export const preinit = runtime.preinit;
export const preinitModule = runtime.preinitModule;
export const preload = runtime.preload;
export const preloadModule = runtime.preloadModule;
export const requestFormReset = runtime.requestFormReset;
export const useFormState = runtime.useFormState;
export const useFormStatus = runtime.useFormStatus;
