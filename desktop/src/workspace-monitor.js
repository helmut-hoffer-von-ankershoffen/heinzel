import { REATTACH_DELAYS_MS } from "./reattach.js";

const terminal = new Set(["paused", "signin", "denied", "address"]);
function failurePhase(error) {
  if (error?.status === 401) return "signin";
  if (error?.status === 403) return "denied";
  if ([404, 410].includes(error?.status) || error?.code === "INVALID_LIFECYCLE") return "address";
  if (["ENETUNREACH", "EHOSTUNREACH", "ENETDOWN"].includes(error?.cause?.code ?? error?.code)) return "waiting";
  return error?.status >= 500 ? "unavailable" : "reconnecting";
}

/** Retain the selected renderer throughout read-only recovery. A ticket fences every asynchronous callback. */
export function createWorkspaceMonitor({ probe, changed, lost, recovered, observed = () => {},
  schedule = setTimeout, unschedule = clearTimeout, random = Math.random }) {
  let target = null, phase = null, timer = null, attempt = null;
  let misses = 0, backoff = 0, awake = true, held = false, generation = 0;
  let localWatch = false, deferredLoss = false;

  function invalidate() {
    generation++;
    if (timer !== null) unschedule(timer);
    timer = null;
    attempt?.abort();
    attempt = null;
  }
  function arm(delay) {
    if (!target || !awake || held || terminal.has(phase) || phase === "waiting") return;
    if (!phase && !target.remote && !localWatch) return;
    timer = schedule(() => { timer = null; void check(); }, delay);
    timer?.unref?.();
  }
  async function check() {
    if (!target || !awake || held || attempt || terminal.has(phase)) return;
    const ticket = generation, connection = target;
    attempt = new AbortController();
    try {
      const facts = await probe(connection, attempt.signal);
      if (ticket !== generation) return;
      const restoring = Boolean(phase);
      phase = null; misses = backoff = 0;
      observed(connection, facts);
      if (restoring) recovered(connection, facts);
      changed();
    } catch (error) {
      if (ticket !== generation) return;
      if (error?.code === "SESSION_CHANGED") {
        misses = 0;
        if (phase === "waiting") { phase = "reconnecting"; changed(); }
        arm(5000);
        return;
      }
      const failure = failurePhase(error);
      if (phase || ++misses >= 3 || terminal.has(failure)) {
        const first = !phase;
        phase = failure;
        if (first) lost(connection);
        changed();
        if (first && !terminal.has(phase) && phase !== "waiting") {
          attempt = null;
          arm(0);
          return;
        }
      }
    } finally { if (ticket === generation) attempt = null; }
    if (ticket !== generation) return;
    const delay = phase ? REATTACH_DELAYS_MS[Math.min(backoff++, REATTACH_DELAYS_MS.length - 1)] : 5000;
    arm(phase ? Math.min(40000, Math.round(delay * (0.9 + random() * 0.2))) : delay);
  }
  function wake() {
    if (!target || !awake || held || attempt || terminal.has(phase)) return;
    invalidate();
    if (deferredLoss) { deferredLoss = false; lost(target); }
    void check();
  }
  return {
    state: () => phase && target ? { origin: target.origin, phase } : null,
    select(connection) {
      invalidate(); target = connection; phase = null; held = false; misses = backoff = 0; localWatch = deferredLoss = false;
      arm(5000);
    },
    lostLocal(connection) {
      if (connection !== target || phase) return;
      invalidate(); localWatch = true; phase = "reconnecting"; deferredLoss = true; changed(); wake();
    },
    pause(origin) {
      if (origin !== target?.origin || !phase || terminal.has(phase)) return;
      invalidate(); phase = "paused"; changed();
    },
    resume(origin) {
      if (origin !== target?.origin || phase !== "paused") return;
      phase = "reconnecting"; backoff = 0; changed(); wake();
    },
    hold(value) {
      if (held === value) return;
      held = value; invalidate();
      if (!held) wake();
    },
    awake(value) {
      if (awake === value) return;
      awake = value; invalidate();
      if (awake) { misses = 0; wake(); }
    },
    wake,
  };
}
