import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { config, noTeam, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { heinzelDanceSvg } from "./heinzel";

const quiet = {
  ...shell,
  "/api/system/config/show": {
    ...config,
    settings: [
      ...config.settings,
      {
        section: "hz",
        key: "playful",
        value: false,
        default: true,
        source: "user",
        env: "HZ_PLAYFUL",
        description: "Play along.",
      },
    ],
  },
};

describe("Heinzelmännchen in the empty moments", () => {
  it("peeks out of the empty team page, beside the words and the command", async () => {
    mockApi({ ...shell, "/api/team/show": noTeam });
    const { container } = renderAt("/team");
    expect(await screen.findByText("hz team new <name>")).toBeInTheDocument();
    await waitFor(() => expect(container.querySelector('svg[data-state="idle"]')).not.toBeNull());
    expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    await expectAccessible(container);
  });

  it("is a guide on a page that does not exist, and the way home stays a link", async () => {
    mockApi(shell);
    const { container } = renderAt("/nowhere");
    expect(await screen.findByRole("link", { name: "Go home" })).toBeInTheDocument();
    await waitFor(() => expect(container.querySelector('svg[data-state="waiting"]')).not.toBeNull());
    await expectAccessible(container.querySelector("main") ?? container);
  });

  it("is blocked beside an error, which still shows in words", async () => {
    mockApi({ ...shell, "/api/team/show": { status: 422, body: { error: "boom" } } });
    const { container } = renderAt("/team");
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load this:");
    await waitFor(() => expect(container.querySelector('svg[data-state="blocked"]')).not.toBeNull());
  });

  it("stays away when the playful setting is off", async () => {
    mockApi({ ...quiet, "/api/team/show": noTeam });
    const { container } = renderAt("/team");
    expect(await screen.findByText("hz team new <name>")).toBeInTheDocument();
    await new Promise((done) => setTimeout(done, 50));
    expect(container.querySelector('svg[data-view="full"]')).toBeNull();
  });
});

describe("loading-dance.svg", () => {
  const file = readFileSync(resolve("public/loading-dance.svg"), "utf-8");

  it("is what the drawing makes (make icons)", () => {
    expect(file).toBe(heinzelDanceSvg());
  });

  it("loops for 1.2 seconds, is pure SVG and CSS and stands still under reduced motion", () => {
    expect(file).not.toMatch(/<script|<image|href=/);
    expect(file).toContain("animation: hz-dance 1.2s");
    expect(file.indexOf("prefers-reduced-motion: no-preference")).toBeLessThan(file.indexOf("animation: hz-dance"));
    expect((file.match(/class="dancer/g) ?? []).length).toBe(4);
  });
});
