// Meet your first Heinzelmännchen: the look editor by part and by list, Surprise me and Undo, the mission, a11y.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { expectAccessible, mockApi } from "@/test/render";
import { MeetHeinzel } from "./MeetHeinzel";

const helmi = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";

function show(onCreated: (id: string) => void = () => {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MeetHeinzel onCreated={onCreated} />
    </QueryClientProvider>,
  );
}

const hat = () => screen.getByRole("button", { name: /^Hat/ });
const nameField = () => screen.getByLabelText("What do you call it?") as HTMLInputElement;
const figure = () => document.querySelector("svg[role=img]")?.outerHTML ?? "";

afterEach(() => {
  localStorage.clear();
});

describe("Meet your first Heinzelmännchen", () => {
  it("suggests a name, and Surprise me changes the look every time, with Undo", async () => {
    const user = userEvent.setup();
    show();
    expect(nameField().value).toBe("Helmi");
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
    const looks = [figure()];
    for (const _ of [1, 2, 3, 4, 5, 6]) {
      // Serial on purpose: each click must differ from the look the one before left.
      await user.click(screen.getByRole("button", { name: "Surprise me" }));
      const now = figure();
      expect(now).not.toBe(looks[looks.length - 1]);
      looks.push(now);
    }
    expect(nameField().value).not.toBe("Helmi");
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(figure()).toBe(looks[5]);
    // Undo steps are serial on purpose: each click changes the button and the figure the next one sees.
    for (const _ of [4, 3, 2, 1, 0]) await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(figure()).toBe(looks[0]);
    expect(nameField().value).toBe("Helmi");
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
  });

  it("keeps a name you typed through Surprise me and Undo", async () => {
    const user = userEvent.setup();
    show();
    await user.clear(nameField());
    await user.type(nameField(), "Ada");
    await user.click(screen.getByRole("button", { name: "Surprise me" }));
    expect(nameField().value).toBe("Ada");
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(nameField().value).toBe("Ada");
  });

  it("changes a part by clicking the figure, and the same part by the list", async () => {
    const user = userEvent.setup();
    show();
    const before = figure();
    await user.click(screen.getByRole("button", { name: "Change the hat" }));
    expect(screen.getByRole("button", { name: "Change the hat" })).toHaveAttribute("aria-pressed", "true");
    const hats = screen.getByRole("radiogroup", { name: "Hat" });
    await user.click(within(hats).getByRole("radio", { name: "Blue" }));
    expect(hat()).toHaveTextContent("Blue");
    const afterHat = figure();
    expect(afterHat).not.toBe(before);
    const list = screen.getByRole("heading", { name: "Everything you can change" }).closest("section") as HTMLElement;
    await user.click(within(list).getByRole("button", { name: /^Build/ }));
    await user.click(within(screen.getByRole("radiogroup", { name: "Build" })).getByRole("radio", { name: "Round" }));
    expect(within(list).getByRole("button", { name: /^Build/ })).toHaveTextContent("Round");
    expect(figure()).not.toBe(afterHat);
    await user.click(within(list).getByRole("button", { name: /^Accessories/ }));
    await user.click(screen.getByRole("checkbox", { name: "Scarf" }));
    expect(within(list).getByRole("button", { name: /^Accessories/ })).toHaveTextContent("Scarf");
    expect(figure()).toContain("with a scarf".replace("with a ", "wearing a "));
  });

  it("offers the missions with one-line first-person jobs, Your day recommended", async () => {
    const user = userEvent.setup();
    show();
    expect(screen.getByRole("radio", { name: /Your day \(Recommended\)/ })).toBeChecked();
    expect(screen.getAllByText("I am your day's assistant. I keep your day in order.")).not.toHaveLength(0);
    await user.click(screen.getByRole("radio", { name: /Your reading/ }));
    expect(screen.getByText(/I keep your reading close/)).toBeInTheDocument();
  });

  it("starts with the look and the name it shows", async () => {
    const bodies: unknown[] = [];
    mockApi({
      "/api/heinzel/new-chat": async (request: Request) => {
        bodies.push(await request.clone().json());
        return { session_id: helmi, heinzel_id: helmi, name: "Helmi", messages: [] };
      },
      "/api/heinzel/sessions": { sessions: [] },
    });
    const user = userEvent.setup();
    const created: string[] = [];
    show((id) => created.push(id));
    await user.click(screen.getByRole("button", { name: "Change the hat" }));
    await user.click(within(screen.getByRole("radiogroup", { name: "Hat" })).getByRole("radio", { name: "Teal" }));
    await user.click(screen.getByRole("button", { name: "Start" }));
    await screen.findByRole("button", { name: "Start" });
    expect(created).toEqual([helmi]);
    expect(bodies[0]).toMatchObject({ name: "Helmi", appearance: { hat: "teal", age: "grown" } });
  });

  it.each([375, 1440])("has no accessibility violations at %i px", async (width) => {
    window.innerWidth = width;
    const { container } = show();
    await expectAccessible(container);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Surprise me" }));
    await expectAccessible(container);
  });
});
