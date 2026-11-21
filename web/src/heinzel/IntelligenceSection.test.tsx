import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SchemaLevelView } from "@/api/client";
import { Providers, queryClient } from "@/app/providers";
import { expectAccessible, mockApi } from "@/test/render";
import { IntelligenceSection, sourceSentence, withoutModels } from "./IntelligenceSection";

const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
type Source = "exact" | "declared" | "nearest" | "provider-default";
type Ran = "low" | "medium" | "high";

function view(
  level: SchemaLevelView["level"] = "medium",
  over: Partial<SchemaLevelView> = {},
  run: { ran?: Ran; source?: Source } = {},
): SchemaLevelView {
  const ran = run.ran ?? (level === "auto" ? "medium" : level);
  return {
    level,
    model: null,
    problem: null,
    profile_revision: 3,
    provider: null,
    session_id: id,
    runs_as: {
      effort: null,
      model: "secret-model-id",
      provider: null,
      level: {
        asked: level,
        ran,
        rule: level === "auto" ? "auto-fixed-medium" : "fixed",
        source: run.source ?? "declared",
      },
    },
    ...over,
  };
}
const option = (name: string, title: string, over: Record<string, unknown> = {}) => ({
  name,
  title,
  module: "system.runner",
  plugin: null,
  in_use: false,
  status: "tested",
  summary: "",
  core_engine: false,
  needs: "",
  detail: "",
  fix: "",
  ...over,
});
const access = {
  selected: "claude-code",
  uses: [],
  options: [
    option("claude-code", "Claude Code", { in_use: true }),
    option("acme", "Acme models", {
      plugin: "acme",
      models: ["acme-fast", "acme-deep", "acme-quiet"],
      unavailable_models: ["acme-quiet"],
    }),
    option("model-api", "Model API", { status: "needs setup" }),
  ],
};

type Saved = Record<string, unknown>;
function serve(initial: SchemaLevelView, fail: string | null = null) {
  let stored = initial;
  const saved: Saved[] = [];
  const mock = mockApi({
    "/api/heinzel/level": () => stored,
    "/api/ai/access/list": access,
    "/api/heinzel/set-level": async (request: Request) => {
      const body = (await request.json()) as Saved;
      if (fail) return { status: 409, body: { error: fail } };
      saved.push(body);
      stored = {
        ...stored,
        ...(body.level ? { level: body.level as SchemaLevelView["level"] } : {}),
        ...(body.provider !== undefined && body.provider !== null
          ? { provider: (body.provider as string) || null }
          : {}),
        ...(body.model !== undefined && body.model !== null ? { model: (body.model as string) || null } : {}),
        profile_revision: stored.profile_revision + 1,
      };
      return stored;
    },
  });
  return { saved, calls: mock.calls };
}
const show = () =>
  render(
    <Providers client={queryClient()}>
      <IntelligenceSection sessionId={id} />
    </Providers>,
  );
const levels = () => screen.getByRole("radiogroup", { name: "How hard it thinks" });

describe("IntelligenceSection", () => {
  it("shows the four levels as one radio group with the chosen one checked and roving tabindex", async () => {
    serve(view("medium"));
    const { container } = show();
    const group = within(await screen.findByRole("radiogroup", { name: "How hard it thinks" }));
    expect(group.getAllByRole("radio").map((radio) => radio.textContent)).toEqual(["Auto", "Low", "Medium", "High"]);
    expect(group.getByRole("radio", { name: "Medium" })).toHaveAttribute("aria-checked", "true");
    expect(group.getByRole("radio", { name: "Medium" })).toHaveAttribute("tabindex", "0");
    expect(group.getByRole("radio", { name: "Low" })).toHaveAttribute("tabindex", "-1");
    expect(group.getByRole("radio", { name: "Low" }).className).toContain("min-h-11");
    expect(screen.getByText("Balanced, a good fit for most work.")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("saves a level at once against the revision it read, and Undo puts it back", async () => {
    const server = serve(view("medium"));
    show();
    await screen.findByRole("radiogroup", { name: "How hard it thinks" });
    fireEvent.click(within(levels()).getByRole("radio", { name: "High" }));
    expect(await screen.findByText("Level set to High.")).toBeInTheDocument();
    expect(server.saved[0]).toMatchObject({ session_id: id, level: "high", expected_revision: 3 });
    expect(typeof server.saved[0]?.request_id).toBe("string");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(server.saved).toHaveLength(2));
    expect(server.saved[1]).toMatchObject({ level: "medium", expected_revision: 4 });
    expect(server.saved[1]?.request_id).not.toBe(server.saved[0]?.request_id);
    await waitFor(() =>
      expect(within(levels()).getByRole("radio", { name: "Medium" })).toHaveAttribute("aria-checked", "true"),
    );
    expect(screen.getByText("Back as it was.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("moves and saves with the arrow keys", async () => {
    const server = serve(view("medium"));
    show();
    const medium = within(await screen.findByRole("radiogroup", { name: "How hard it thinks" })).getByRole("radio", {
      name: "Medium",
    });
    medium.focus();
    fireEvent.keyDown(medium, { key: "ArrowRight" });
    expect(within(levels()).getByRole("radio", { name: "High" })).toHaveFocus();
    await waitFor(() => expect(server.saved[0]).toMatchObject({ level: "high" }));
    const high = within(levels()).getByRole("radio", { name: "High" });
    fireEvent.keyDown(high, { key: "ArrowRight" });
    expect(within(levels()).getByRole("radio", { name: "Auto" })).toHaveFocus();
  });

  it("says Auto is computed by code and what it uses", async () => {
    serve(view("auto"));
    show();
    expect(await screen.findByText(/Auto uses Medium for now\. Your provider has a Medium model/)).toBeInTheDocument();
    expect(screen.getByText("Heinzel decides how hard to think, by a fixed rule.")).toBeInTheDocument();
  });

  it("says in words when the provider has no model for the level", async () => {
    serve(view("low", {}, { ran: "medium", source: "nearest" }));
    show();
    expect(await screen.findByText("Your provider has no Low model, so Medium is used.")).toBeInTheDocument();
  });

  it("says in words when the provider sets no levels, and when an exact model wins", async () => {
    serve(view("high", {}, { source: "provider-default" }));
    const first = show();
    expect(await screen.findByText("Your provider sets no levels, so it runs its default.")).toBeInTheDocument();
    first.unmount();
    serve(view("high", { model: "acme-fast", provider: "acme" }, { source: "exact" }));
    show();
    expect(await screen.findByText("The exact model you chose is used.")).toBeInTheDocument();
  });

  it("words every source and has none for a turn that can't start", () => {
    expect(sourceSentence(view("low", {}, { source: "declared" }))).toBe(
      "Your provider has a Low model, so it is used.",
    );
    expect(sourceSentence(view("high", { runs_as: null }))).toBeNull();
  });

  it("says why a turn can't start on this choice", async () => {
    serve(view("high", { runs_as: null, problem: "Sign in to Claude Code." }));
    show();
    expect(await screen.findByText("It can't run on this choice now: Sign in to Claude Code.")).toBeInTheDocument();
  });

  it("never names a model outside the advanced disclosure", async () => {
    serve(view("medium", { model: "acme-fast", provider: "acme" }));
    const { container } = show();
    await screen.findByRole("radiogroup", { name: "How hard it thinks" });
    expect(container.textContent).not.toMatch(/secret-model-id|acme-fast|acme-deep/);
    const toggle = screen.getByRole("button", { name: "Advanced: exact model" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("radio", { name: "acme-fast" })).toBeChecked();
  });

  it("lists the provider's offers: an unavailable one can't be picked, and one is saved with Undo", async () => {
    const server = serve(view("medium", { provider: "acme" }));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Advanced: exact model" }));
    const quiet = screen.getByRole("radio", { name: "acme-quiet (not answering)" });
    expect(quiet).toBeDisabled();
    fireEvent.click(quiet);
    expect(server.saved).toHaveLength(0);
    fireEvent.click(screen.getByRole("radio", { name: "acme-deep" }));
    expect(await screen.findByText("Exact model changed.")).toBeInTheDocument();
    expect(server.saved[0]).toMatchObject({ model: "acme-deep" });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(server.saved[1]).toMatchObject({ model: "" }));
  });

  it("says when the provider lists no models", async () => {
    serve(view("medium"));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Advanced: exact model" }));
    expect(screen.getByText("This provider lists no models, so the level decides.")).toBeInTheDocument();
  });

  it("chooses the AI provider, clears the exact model with it, and Undo restores both", async () => {
    const server = serve(view("medium", { provider: "acme", model: "acme-fast" }));
    show();
    const configured = await screen.findByRole("radio", { name: "As set in Settings (Claude Code)" });
    expect(screen.getByRole("radio", { name: "Acme models" })).toBeChecked();
    expect(screen.queryByRole("radio", { name: "Model API" })).toBeNull();
    expect(screen.queryByRole("radio", { name: "Claude Code" })).toBeNull();
    fireEvent.click(configured);
    expect(await screen.findByText("AI provider changed.")).toBeInTheDocument();
    expect(server.saved[0]).toMatchObject({ provider: "", model: "" });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(server.saved[1]).toMatchObject({ provider: "acme", model: "acme-fast" }));
  });

  it("names the action when saving the level fails, and keeps the old level", async () => {
    serve(view("medium"), "The Heinzel changed; try again.");
    show();
    fireEvent.click(
      within(await screen.findByRole("radiogroup", { name: "How hard it thinks" })).getByRole("radio", { name: "Low" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/^Could not save the level\./);
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    expect(within(levels()).getByRole("radio", { name: "Medium" })).toHaveAttribute("aria-checked", "true");
  });

  it("names the action when changing the provider fails", async () => {
    serve(view("medium", { provider: "acme" }), "no");
    show();
    fireEvent.click(await screen.findByRole("radio", { name: "As set in Settings (Claude Code)" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/^Could not change the AI provider\./);
  });

  it("names the action when loading fails", async () => {
    mockApi({
      "/api/heinzel/level": { status: 404, body: { detail: "no such Heinzel" } },
      "/api/ai/access/list": access,
    });
    show();
    expect((await screen.findByRole("alert")).textContent).toMatch(/^Could not load this Heinzel's intelligence\./);
  });

  it("never names the exact model in a problem sentence while the disclosure is closed", async () => {
    serve(
      view("medium", {
        provider: "acme",
        model: "acme-fast",
        runs_as: null,
        problem: "Couldn't run this Heinzel on the exact model acme-fast: it did not answer.",
      }),
    );
    const { container } = show();
    expect(
      await screen.findByText(/It can't run on this choice now: Couldn't run this Heinzel on the exact model/),
    ).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/acme-fast|acme-deep/);
  });

  it("never names a model in a save error while the disclosure is closed", async () => {
    serve(view("medium", { provider: "acme", model: "acme-fast" }), "acme-fast is not offered any more");
    const { container } = show();
    fireEvent.click(await screen.findByRole("radio", { name: "As set in Settings (Claude Code)" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /^Could not change the AI provider\. \(see Advanced\) is not/,
    );
    expect(container.textContent).not.toMatch(/acme-fast/);
    expect(withoutModels("a-b and a-bc", ["a-b", "a-bc"])).toBe("(see Advanced) and (see Advanced)");
  });

  it("keeps the keyboard in place: radios stay focusable while saving, and Undo hands focus to the level", async () => {
    serve(view("medium", { provider: "acme" }));
    show();
    const configured = await screen.findByRole("radio", { name: "As set in Settings (Claude Code)" });
    configured.focus();
    fireEvent.click(configured);
    expect(configured).not.toBeDisabled();
    expect(await screen.findByText("AI provider changed.")).toBeInTheDocument();
    expect(document.activeElement).toBe(configured);
    const undo = screen.getByRole("button", { name: "Undo" });
    undo.focus();
    fireEvent.click(undo);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Undo" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(within(levels()).getByRole("radio", { name: "Medium" })));
  });

  it("keeps focus on the level group when Undo fails too", async () => {
    serve(view("medium"));
    show();
    fireEvent.click(
      within(await screen.findByRole("radiogroup", { name: "How hard it thinks" })).getByRole("radio", {
        name: "High",
      }),
    );
    const undo = await screen.findByRole("button", { name: "Undo" });
    mockApi({
      "/api/heinzel/level": view("high"),
      "/api/ai/access/list": access,
      "/api/heinzel/set-level": { status: 409, body: { detail: "changed" } },
    });
    undo.focus();
    fireEvent.click(undo);
    expect((await screen.findByRole("alert")).textContent).toMatch(/^Could not undo the change\./);
    expect(document.activeElement?.getAttribute("role")).toBe("radio");
  });

  it("shows a saved exact model the provider no longer offers as the checked row", async () => {
    serve(view("medium", { provider: "acme", model: "acme-retired" }));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Advanced: exact model" }));
    expect(screen.getByRole("radio", { name: "acme-retired (not offered now)" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Let the level choose" })).not.toBeChecked();
  });
});
