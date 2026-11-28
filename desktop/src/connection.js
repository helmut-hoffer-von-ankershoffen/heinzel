export function parseConnection(args) {
  const argument = args.find((item) => item.startsWith("--hz-url="));
  if (!argument) throw new Error("Pass an existing local UI URL using --hz-url.");
  const url = new URL(argument.slice("--hz-url=".length));
  const token = url.hash.match(/^#token=([A-Za-z0-9_-]{32,})$/)?.[1];
  if (
    url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port ||
    url.username || url.password || url.search || !token || url.pathname !== "/"
  ) throw new Error("The URL must be an authenticated Heinzelmännchen loopback UI address.");
  return Object.freeze({ origin: url.origin, token });
}

export async function lifecycle(connection, fetchImpl = fetch, signal) {
  const response = await fetchImpl(`${connection.origin}/api/lifecycle`, {
    headers: { Authorization: `Bearer ${connection.token}`, Accept: "application/json" },
    redirect: "error",
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(5000)]) : AbortSignal.timeout(5000),
  });
  if (!response.ok) throw Object.assign(new Error("The workspace refused authentication or is unavailable."), { status: response.status });
  const value = await response.json();
  if (!value || typeof value !== "object" || typeof value.instance !== "string" || !value.instance.trim() || typeof value.version !== "string" || !value.version.trim())
    throw Object.assign(new Error("The workspace returned an invalid lifecycle response."), { code: "INVALID_LIFECYCLE" });
  return Object.freeze({ instance: value.instance, version: value.version, restartable: value.restartable === true });
}
