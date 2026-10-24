// The typed client: paths and models come from web/openapi.json, which `make fix` regenerates from the operations.
import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";
import { token } from "./token";

export type * from "./schema";

/** A failed call, with the message the API head wrote for a person (`{"error": …}`) when it wrote one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
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

// Absolute, from this page's origin: the API is always on the same origin (ADR 0039).
// `fetch` looked up per call, not captured once, so tests can answer it.
export const api = createClient<paths>({
  baseUrl: `${window.location.origin}/api`,
  fetch: (request) => globalThis.fetch(request),
});
api.use(bearer);

/** Why a call failed, in words: the API's `error`, a validation detail, or the HTTP status. */
export function problem(status: number, body: unknown): ApiError {
  if (body && typeof body === "object") {
    if ("error" in body && typeof body.error === "string") return new ApiError(status, body.error);
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
  throw problem(response.status, error);
}
