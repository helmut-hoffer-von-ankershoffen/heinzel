// Render a screen inside the app's routes and providers, with the API answered by `mockApi`.
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, vi } from "vitest";
import { routes } from "@/App";
import { Providers, queryClient } from "@/app/providers";

const footerObservers = new Set<MutationObserver>();
afterEach(() => {
  for (const observer of footerObservers) observer.disconnect();
  footerObservers.clear();
});

const footerContentWidth = (element: HTMLElement) => (element.childElementCount ? 24 : 0);

export function renderAt(path: string, client = queryClient()) {
  sessionStorage.setItem("hz.token", "test-token");
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const client = queryClient();
  const view = render(
    <Providers client={client}>
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
          new DOMRect(0, 0, element.tagName === "FOOTER" ? window.innerWidth : footerContentWidth(element), 24);
      }
    };
    measure();
    const observer = new MutationObserver(measure);
    observer.observe(view.container, { childList: true, subtree: true });
    footerObservers.add(observer);
  }
  return { ...view, client };
}
