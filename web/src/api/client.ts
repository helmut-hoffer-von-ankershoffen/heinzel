// The typed client: paths and models come from web/openapi.json, which `make fix` regenerates from the operations.
import createClient, { type Middleware } from "openapi-fetch";
import { dropRefusedDevice } from "./device";
import type { paths } from "./schema";
import { authorityEpoch, deviceToken, sessionToken, token } from "./token";

export type * from "./schema";

/** A failed call, with the message the API head wrote for a person (`{"error": …}`) when it wrote one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
  }
}

const bearer: Middleware = {
  onRequest({ request }) {
    const value = token();
    if (value) request.headers.set("Authorization", `Bearer ${value}`);
    return request;
  },
};

const pinnedRequests = new WeakSet<Request>();
const readResponses = new WeakSet<Response>();
const readFailures = new WeakSet<object>();

/** The restore signal refetches only active queries whose failing operation was an ordinary workspace read. */
export function failedWorkspaceRead(error: unknown): boolean {
  return typeof error === "object" && error !== null && readFailures.has(error);
}

async function fetchWorkspace(request: Request): Promise<Response> {
  const ordinaryRead =
    (request.method === "GET" || request.method === "HEAD") &&
    new URL(request.url).origin === window.location.origin &&
    !pinnedRequests.has(request);
  const sent = request.headers.get("Authorization")?.replace(/^Bearer /, "") ?? null;
  const paired = ordinaryRead && sent && sent === sessionToken() ? deviceToken() : null;
  try {
    let response = await globalThis.fetch(request);
    if (response.status === 401) {
      await dropRefusedDevice(401, sent);
      if (paired && !sessionToken() && token() === paired && !request.signal.aborted) {
        const revision = authorityEpoch();
        const headers = new Headers(request.headers);
        headers.set("Authorization", `Bearer ${paired}`);
        response = await globalThis.fetch(
          new Request(request, { headers, mode: "same-origin", redirect: "error", cache: "no-store" }),
        );
        if (revision !== authorityEpoch()) throw new Error("Browser authority changed");
        await dropRefusedDevice(response.status, paired);
      }
    }
    if (ordinaryRead && !response.ok) readResponses.add(response);
    return response;
  } catch (error) {
    if (ordinaryRead && error instanceof TypeError && !request.signal.aborted) readFailures.add(error);
    throw error;
  }
}

// Absolute, from this page's origin: the API is always on the same origin (ADR 0039).
// `fetch` looked up per call, not captured once, so tests can answer it.
export const api = createClient<paths>({
  baseUrl: `${window.location.origin}/api`,
  fetch: fetchWorkspace,
});
api.use(bearer);

/** Reject a queued request if its original browser authority has been replaced. */
export function authorityMiddleware(expected: number): Middleware[] {
  return [
    {
      onRequest({ request }) {
        if (authorityEpoch() !== expected) throw new Error("Browser authority changed");
        const value = token();
        if (!value) throw new Error("Browser authority is unavailable");
        pinnedRequests.add(request);
        request.headers.set("Authorization", `Bearer ${value}`);
        return request;
      },
    },
  ];
}

/** Why a call failed, in words: the API's `error`, a validation detail, or the HTTP status. */
export function problem(status: number, body: unknown): ApiError {
  if (body && typeof body === "object") {
    if ("error" in body && typeof body.error === "string")
      return new ApiError(status, body.error, "code" in body && typeof body.code === "string" ? body.code : undefined);
    if ("detail" in body && Array.isArray(body.detail)) {
      const details = body.detail.map((item: { msg?: string }) => item.msg ?? "").filter(Boolean);
      if (details.length) return new ApiError(status, details.join("; "));
    }
  }
  return new ApiError(status, `HTTP ${status}`);
}

/** The data of a call, or an ApiError. A 503 with a body is a result whose exit code is non-zero (doctor DOWN). */
export async function unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (data !== undefined) return data;
  if (response.status === 503 && error && typeof error === "object" && !("error" in error)) return error as T;
  const failure = problem(response.status, error);
  if (readResponses.has(response)) readFailures.add(failure);
  throw failure;
}
