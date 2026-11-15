// Render a screen inside the app's providers, with the API answered by made-up data.
import { render } from "@testing-library/react";
import axe from "axe-core";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, vi } from "vitest";
import { Providers, queryClient, routes } from "@/App";

export type Answer = { status?: number; body: unknown };
type AnswerSource = unknown | Answer | ((request: Request) => unknown | Answer | Promise<unknown | Answer>);

/** Answer `fetch` by path (`/api/system/doctor`): a body for 200, or `{status, body}`; anything else is a 404. */
export function mockApi(answers: Record<string, AnswerSource>): { calls: Request[] } {
  const calls: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      // `POST /api/stream/<path>` runs the operation at `/api/<path>` (ADR 0045): with no answer of its own, the
      // plain answer is the stream's result or error, and the call is recorded as the plain call it runs.
      const plain = `/api${url.pathname.slice("/api/stream".length)}`;
      const streamed = url.pathname.startsWith("/api/stream/") && !(url.pathname in answers) && plain in answers;
      const input = streamed
        ? new Request(new URL(`${plain}${url.search}`, url), {
            method: request.method,
            headers: request.headers,
            body: await request.text(),
          })
        : request;
      calls.push(input);
      const path = new URL(input.url).pathname;
      let answer = answers[path];
      if (typeof answer === "function") answer = await answer(input);
      let response: Response;
      if (answer instanceof Response) response = answer;
      else if (answer === undefined) response = Response.json({ error: `no answer for ${path}` }, { status: 404 });
      else {
        const { status, body } =
          answer && typeof answer === "object" && "body" in answer ? (answer as Answer) : { status: 200, body: answer };
        response = Response.json(body, { status: status ?? 200 });
      }
      return streamed ? asStream(response) : response;
    }),
  );
  return { calls };
}

/** A plain answer as the stream of the same operation: `result` with its JSON, or `error` with its body. */
async function asStream(response: Response): Promise<Response> {
  const body = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  return response.ok ? eventStream([], body) : eventFailure(body);
}

/** A stream that ends with an operation's error, as the web head streams a `HeinzelError` (`heads/_stream.py`). */
export function eventFailure(error: object): Response {
  return new Response(`event: error\ndata: ${JSON.stringify(error)}\n\n`, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

/** A finished server-sent event stream of an operation (`POST /api/stream/...`): its events, then its result. */
export function eventStream(events: [string, object][], result: object = {}): Response {
  const frames = [...events, ["result", { data: result }] as [string, object]].map(
    ([kind, data]) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`,
  );
  return new Response(frames.join(""), { headers: { "Content-Type": "text/event-stream" } });
}

const footerObservers = new Set<MutationObserver>();
afterEach(() => {
  for (const observer of footerObservers) observer.disconnect();
  footerObservers.clear();
});

export function renderAt(path: string) {
  sessionStorage.setItem("hz.token", "test-token");
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <Providers client={queryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  // jsdom supplies zero geometry. Measure only footer fixture nodes, leaving every other element's geometry and
  // per-instance spies untouched. Geometry-specific tests keep their own explicit prototype measurements.
  if (!vi.isMockFunction(Element.prototype.getBoundingClientRect)) {
    const measure = () => {
      for (const element of view.container.querySelectorAll<HTMLElement>("footer,[data-footer-content]")) {
        if (Object.hasOwn(element, "getBoundingClientRect")) continue;
        element.getBoundingClientRect = () =>
          new DOMRect(0, 0, element.tagName === "FOOTER" ? window.innerWidth : element.childElementCount ? 24 : 0, 24);
      }
    };
    measure();
    const observer = new MutationObserver(measure);
    observer.observe(view.container, { childList: true, subtree: true });
    footerObservers.add(observer);
  }
  return view;
}

/** Fail on any WCAG 2.x A/AA violation axe finds (ADR 0039); colour contrast needs a real browser (Playwright). */
export async function expectAccessible(container: HTMLElement) {
  const results = await axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    rules: { "color-contrast": { enabled: false } },
  });
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([]);
}
