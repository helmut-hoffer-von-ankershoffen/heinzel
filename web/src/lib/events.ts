// Structured events in the browser, in ADR 0138's vocabulary: the event name `area.operation.step` and fields
// (operation, step, phase, outcome, duration_ms, version, reason, error_type, error), one JSON object per console line.
// Fields that name a secret are redacted by key, as the kernel's helper does; undefined fields are left out.
export type EventLevel = "debug" | "info" | "warn" | "error";

const SECRET = /token|secret|password|api.?key|authorization|cookie|credential|private|session.?key|email/i;

export function eventLine(event: string, fields: Record<string, unknown>): string {
  const kept = Object.entries(fields)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => [key, SECRET.test(key) ? "[redacted]" : value]);
  return JSON.stringify({ event, ...Object.fromEntries(kept) });
}

/** Log one event; logging never throws into the code it describes. */
export function logEvent(event: string, fields: Record<string, unknown> = {}, level: EventLevel = "info"): void {
  try {
    console[level](eventLine(event, fields));
  } catch {
    // no console: nothing to tell
  }
}
