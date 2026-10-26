// Render a screen inside the app's providers, with the API answered by made-up data.
import { render } from "@testing-library/react";
import axe from "axe-core";
import { createMemoryRouter, RouterProvider } from "react-router";
import { expect, vi } from "vitest";
import { Providers, queryClient, routes } from "@/App";

export type Answer = { status?: number; body: unknown };
type AnswerSource = unknown | Answer | ((request: Request) => unknown | Answer | Promise<unknown | Answer>);

/** Answer `fetch` by path (`/api/system/doctor`): a body for 200, or `{status, body}`; anything else is a 404. */
export function mockApi(answers: Record<string, AnswerSource>): { calls: Request[] } {
  const calls: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      calls.push(input);
      const path = new URL(input.url).pathname;
      let answer = answers[path];
      if (typeof answer === "function") answer = await answer(input);
      if (answer === undefined) return Response.json({ error: `no answer for ${path}` }, { status: 404 });
      const { status, body } =
        answer && typeof answer === "object" && "body" in answer ? (answer as Answer) : { status: 200, body: answer };
      return Response.json(body, { status: status ?? 200 });
    }),
  );
  return { calls };
}

export function renderAt(path: string) {
  sessionStorage.setItem("hz.token", "test-token");
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <Providers client={queryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
}

/** Fail on any WCAG 2.x A/AA violation axe finds (ADR 0039); colour contrast needs a real browser (Playwright). */
export async function expectAccessible(container: HTMLElement) {
  const results = await axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    rules: { "color-contrast": { enabled: false } },
  });
  expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([]);
}
