import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SchemaSettingInfo } from "@/api/client";
import { SchemaValue } from "./SchemaValue";
import { Row } from "./SettingControls";
import { controlFor, unwrap } from "./settingSchema";

const mutate = vi.fn();
const mutateAsync = vi.fn();
const capabilities = vi.hoisted(() => ({ pending: false }));
vi.mock("@/api/queries", () => ({
  useSetSetting: () => ({ mutate, mutateAsync, isPending: false }),
  useCapabilities: () => ({
    data: capabilities.pending
      ? undefined
      : {
          capabilities: [
            { name: "calendar", bound: null, how: "unbound" },
            { name: "mail", bound: "oak", how: "automatic" },
            { name: "tracker", bound: "core-tracker", how: "default" },
            { name: "meetings", bound: "a-very-long-meetings-adapter-name", how: "automatic" },
            { name: "documents", bound: "drive", adapters: ["drive", "wiki"], multiple: true, how: "configured" },
          ],
          adapters: [
            { adapter: "maple", capability: "calendar", distribution: "sample-maple", module: "maple" },
            { adapter: "oak", capability: "mail", distribution: "sample-oak", module: "oak" },
            {
              adapter: "drive",
              capability: "documents",
              distribution: "sample-drive",
              module: "drive",
              links: ["drive.example.test"],
            },
            {
              adapter: "wiki",
              capability: "documents",
              distribution: "sample-wiki",
              module: "wiki",
              links: ["wiki.example.test"],
            },
            {
              adapter: "plain",
              capability: "documents",
              distribution: "sample-plain",
              module: "plain",
              links: ["drive.example.test"],
            },
          ],
        },
  }),
  useConfig: () => ({ data: { can_reveal: false } }),
  useInfo: () => ({ data: { modules: [{ qualname: "maple", summary: "Sample calendar" }] } }),
}));
vi.mock("@/lifecycle/RestartAndApply", () => ({
  RestartAndApply: ({
    changes,
    settings,
    preserve,
    onUnsupported,
    onApplied,
    target,
    deferredMessage,
  }: {
    changes: string[];
    settings?: { section: string; key: string; value: unknown }[];
    preserve: (signal: AbortSignal) => Promise<void>;
    onUnsupported: () => Promise<void>;
    onApplied?: () => void;
    target: string;
    deferredMessage: string;
  }) => (
    <>
      <output data-testid="expected-settings">{JSON.stringify(settings ?? [])}</output>
      <output data-testid="restart-target">{target}</output>
      <output data-testid="deferred-message">{deferredMessage}</output>
      <button
        type="button"
        onClick={async () => {
          await preserve(new AbortController().signal);
          onApplied?.();
        }}
      >
        Restart and apply: {changes.join(", ")}
      </button>
      <button type="button" onClick={() => void onUnsupported().catch(() => undefined)}>
        Unsupported restart
      </button>
    </>
  ),
}));

beforeEach(() => {
  mutate.mockClear();
  mutateAsync.mockReset().mockResolvedValue({});
});

const setting = (source: string): SchemaSettingInfo => ({
  section: "sample",
  key: "limit",
  description: "Limit",
  env: "HZ_SAMPLE__LIMIT",
  source,
  value: 8,
  default: 3,
  json_schema: { type: "number" },
});

const enabled = (value: boolean): SchemaSettingInfo => ({
  ...setting("user"),
  key: "enabled",
  value,
  default: false,
  json_schema: { type: "boolean" },
});

describe("Schema setting metadata", () => {
  it("preserves outer nullable annotations without replacing branch constraints", () => {
    const schema = unwrap({
      anyOf: [{ type: "string", minItems: 1 }, { type: "null" }],
      format: "iana-timezone",
      default: null,
    });
    expect(schema).toMatchObject({ type: "string", format: "iana-timezone", minItems: 1, default: null });
    expect(controlFor(schema)).toEqual({ kind: "timezone" });
  });

  it("uses declared friendly labels while saving the enum value", async () => {
    const onChange = vi.fn();
    render(
      <SchemaValue
        label="Channel"
        schema={{
          type: "string",
          enum: ["release", "main"],
          "x-labels": { release: "Releases", main: "Newest commit" },
        }}
        value="release"
        disabled={false}
        onChange={onChange}
      />,
    );
    await userEvent
      .setup()
      .selectOptions(
        screen.getByRole("combobox", { name: "Channel" }),
        screen.getByRole("option", { name: "Newest commit" }),
      );
    expect(onChange).toHaveBeenCalledWith("main");
  });

  it("formats hours from schema metadata even with a translated label", () => {
    render(
      <SchemaValue
        label="Arbeitszeit"
        schema={{ type: "integer", minimum: 0, maximum: 24, "x-unit": "hour-of-day" }}
        value={9}
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("option", { name: "09:00" })).toHaveValue("9");
  });

  it("does not infer clock times from an English field name", () => {
    render(
      <SchemaValue
        label="Hours retained"
        schema={{ type: "integer", minimum: 1, maximum: 24 }}
        value={9}
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("option", { name: "9" })).toHaveValue("9");
  });

  it("does not offer a user-scope reset for a project value", () => {
    render(<Row setting={setting("project")} />);
    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
  });

  it("does not remove a user override until the shared restart-and-apply confirmation", async () => {
    render(<Row setting={setting("user")} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(mutateAsync).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /Restart and apply: Limit/ }));
    expect(mutateAsync).toHaveBeenCalledExactlyOnceWith({ key: "sample.limit", value: null });
  });

  it("stages an ordinary switch value until the shared confirmation is pressed", async () => {
    render(
      <Row
        setting={{
          ...setting("user"),
          key: "enabled",
          value: true,
          default: false,
          json_schema: { type: "boolean" },
        }}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    expect(mutateAsync).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /Restart and apply: Enabled/ }));
    expect(mutateAsync).toHaveBeenCalledExactlyOnceWith({ key: "sample.enabled", value: "false" });
  });

  it("shows a staged adapter choice as chosen until it is applied or cancelled", async () => {
    render(
      <Row
        setting={{
          ...setting("default"),
          section: "capabilities",
          key: "calendar",
          description: "Calendar",
          value: null,
          default: null,
          json_schema: {
            type: "string",
            title: "Calendar",
            "x-options": { source: "adapters", capability: "calendar", none: "none" },
          },
        }}
      />,
    );
    const user = userEvent.setup();
    const choice = screen.getByRole("combobox", { name: "Calendar" });
    await user.selectOptions(choice, "maple");
    expect(choice).toHaveValue("maple");
    expect(mutateAsync).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("combobox", { name: "Calendar" })).toHaveValue("");
  });

  it("drops a staged change and says so when the value changed elsewhere meanwhile", async () => {
    const { rerender } = render(<Row setting={enabled(true)} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    expect(screen.getByRole("button", { name: /Restart and apply: Enabled/ })).toBeInTheDocument();
    rerender(<Row setting={enabled(false)} />);
    expect(screen.queryByRole("button", { name: /Restart and apply: Enabled/ })).not.toBeInTheDocument();
    const notice = screen.getByText(/Changed elsewhere/);
    expect(notice).toHaveAttribute("role", "status");
    expect(notice.textContent?.length).toBeLessThanOrEqual(60);
    expect(mutateAsync).not.toHaveBeenCalled();
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    expect(screen.queryByText(/Changed elsewhere/)).not.toBeInTheDocument();
  });

  it.each(["Unsupported restart", "Restart and apply: Enabled"])(
    "never flags its own save as changed elsewhere, even when the refetch lands first (%s)",
    async (button) => {
      const { rerender } = render(<Row setting={enabled(true)} />);
      mutateAsync.mockImplementation(async () => {
        rerender(<Row setting={enabled(false)} />);
        await new Promise((resolve) => setTimeout(resolve, 0));
        return {};
      });
      const user = userEvent.setup();
      await user.click(screen.getByRole("switch", { name: "Enabled" }));
      await user.click(screen.getByRole("button", { name: button }));
      await new Promise((resolve) => setTimeout(resolve, 10));
      expect(screen.queryByText(/Changed elsewhere/)).not.toBeInTheDocument();
    },
  );

  it("watches for a change elsewhere again when its own save failed", async () => {
    const { rerender } = render(<Row setting={enabled(true)} />);
    mutateAsync.mockRejectedValue(new Error("refused"));
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    await user.click(screen.getByRole("button", { name: "Unsupported restart" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    rerender(<Row setting={enabled(false)} />);
    expect(screen.getByText(/Changed elsewhere/)).toBeInTheDocument();
  });

  it("keeps a change it saved itself staged until applied, not flagged as changed elsewhere", async () => {
    const { rerender } = render(<Row setting={enabled(true)} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    await user.click(screen.getByRole("button", { name: "Unsupported restart" }));
    rerender(<Row setting={enabled(false)} />);
    expect(screen.queryByText(/Changed elsewhere/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Restart and apply: Enabled/ })).toBeInTheDocument();
  });

  it("identifies hz ui and saves only after the shared unsupported confirmation", async () => {
    render(
      <Row
        setting={{ ...setting("user"), key: "enabled", value: true, default: false, json_schema: { type: "boolean" } }}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    expect(screen.getByTestId("restart-target")).toHaveTextContent("hz ui");
    expect(screen.getByTestId("deferred-message")).toHaveTextContent(
      "Saved Enabled. This server cannot restart hz ui. Run `hz ui serve` in a terminal to apply this setting.",
    );
    expect(mutateAsync).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Unsupported restart" }));
    expect(mutateAsync).toHaveBeenCalledExactlyOnceWith({ key: "sample.enabled", value: "false" });
  });

  it("never populates a secret editor with a masked report value", () => {
    render(
      <Row
        setting={{
          ...setting("user"),
          key: "token",
          value: "**********",
          default: null,
          json_schema: { anyOf: [{ type: "string", format: "password", writeOnly: true }, { type: "null" }] },
        }}
      />,
    );
    expect(screen.queryByDisplayValue("**********")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Token")).toHaveAttribute("type", "password");
  });
});

describe("Declared installed choices", () => {
  it("filters capability adapters and saves explicit None as the declared sentinel", async () => {
    const onChange = vi.fn();
    render(
      <SchemaValue
        label="Provider"
        schema={{ type: "string", "x-options": { source: "adapters", capability: "calendar", none: "none" } }}
        value="maple"
        disabled={false}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("option", { name: /maple/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /oak/ })).not.toBeInTheDocument();
    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Provider" }), "none");
    expect(onChange).toHaveBeenCalledWith("none");
  });

  it("shows an unavailable selected adapter without silently replacing it", () => {
    render(
      <SchemaValue
        label="Provider"
        schema={{ type: "string", "x-options": { source: "adapters", capability: "calendar" } }}
        value="missing"
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("combobox", { name: "Provider" })).toHaveValue("missing");
    expect(screen.getByRole("option", { name: /missing.*unavailable/i })).toBeInTheDocument();
  });

  it("says which adapter serves an unset capability, and not set when none does", () => {
    const { unmount } = render(
      <SchemaValue
        label="Mail"
        schema={{ type: "string", "x-options": { source: "adapters", capability: "mail", none: "none" } }}
        value={null}
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("option", { name: "not set: oak serves it, the only one installed" })).toBeInTheDocument();
    unmount();
    render(
      <SchemaValue
        label="Calendar"
        schema={{ type: "string", "x-options": { source: "adapters", capability: "calendar", none: "none" } }}
        value={null}
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("option", { name: "not set" })).toBeInTheDocument();
  });

  it("says the only one installed only when hz chose it automatically, not for a core default", () => {
    render(
      <SchemaValue
        label="Tracker"
        schema={{ type: "string", "x-options": { source: "adapters", capability: "tracker", none: "none" } }}
        value={null}
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("option", { name: "not set" })).toBeInTheDocument();
    expect(screen.queryByText(/the only one installed/)).not.toBeInTheDocument();
  });

  it("cuts a long adapter name short within the pane and shows it whole as a tooltip", () => {
    render(
      <SchemaValue
        label="Meetings"
        schema={{ type: "string", "x-options": { source: "adapters", capability: "meetings", none: "none" } }}
        value={null}
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    const shown = "not set: a-very-long-meet… serves it, the only one installed";
    expect(screen.getByRole("option", { name: shown })).toBeInTheDocument();
    expect(shown.length).toBeLessThanOrEqual(60);
    expect(screen.getByRole("combobox", { name: "Meetings" })).toHaveAttribute(
      "title",
      "not set: a-very-long-meetings-adapter-name serves it, the only one installed",
    );
  });

  const documents = {
    anyOf: [{ type: "string" }, { type: "array", items: { type: "string" } }, { type: "null" }],
    title: "Documents",
    "x-options": { source: "adapters", capability: "documents", none: "none", multiple: true },
  };

  it("switches several documents connectors on at once and keeps the first as the default", async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <SchemaValue label="Documents" schema={documents} value="drive" disabled={false} onChange={onChange} />,
    );
    const group = screen.getByRole("group", { name: "Documents" });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /drive/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /wiki/ })).not.toBeChecked();
    // A list of switches offers no "None": switching every one off is none.
    expect(screen.queryByRole("checkbox", { name: "None" })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("checkbox", { name: /wiki/ }));
    expect(onChange).toHaveBeenCalledWith(["drive", "wiki"]);
    rerender(
      <SchemaValue
        label="Documents"
        schema={documents}
        value={["drive", "wiki"]}
        disabled={false}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("checkbox", { name: /wiki/ })).toBeChecked();
    expect(screen.getByText(/drive, the first, is the default/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("checkbox", { name: /drive/ }));
    expect(onChange).toHaveBeenLastCalledWith(["wiki"]);
  });

  it("warns when connectors that are on open links on the same host, as doctor does", () => {
    const { rerender } = render(
      <SchemaValue
        label="Documents"
        schema={documents}
        value={["drive", "wiki"]}
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Links refused/)).not.toBeInTheDocument();
    rerender(
      <SchemaValue
        label="Documents"
        schema={documents}
        value={["drive", "plain"]}
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    const warning = screen.getByText("Links refused: two connectors claim the same host");
    expect(warning.textContent?.length).toBeLessThanOrEqual(60);
    expect(warning).toHaveAttribute("title", "drive.example.test: drive, plain");
  });

  it("reads a saved none as every documents connector off", () => {
    render(<SchemaValue label="Documents" schema={documents} value="none" disabled={false} onChange={vi.fn()} />);
    expect(screen.getByRole("checkbox", { name: /drive/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /wiki/ })).not.toBeChecked();
    expect(screen.queryByText(/is the default/)).not.toBeInTheDocument();
  });

  it("chooses the multiple control from the declared options, not only from an array type", () => {
    expect(controlFor(documents)).toMatchObject({ kind: "installed", multiple: true });
    expect(controlFor({ type: "string", "x-options": { source: "adapters", capability: "calendar" } })).toMatchObject({
      kind: "installed",
      multiple: false,
    });
  });

  it("never calls a saved adapter unavailable while the choices are still loading", () => {
    capabilities.pending = true;
    try {
      render(
        <SchemaValue
          label="Provider"
          schema={{ type: "string", "x-options": { source: "adapters", capability: "calendar" } }}
          value="maple"
          disabled={false}
          onChange={vi.fn()}
        />,
      );
      expect(screen.getByRole("combobox", { name: "Provider" })).toHaveValue("maple");
      expect(screen.getByRole("option", { name: "maple" })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: /unavailable/i })).not.toBeInTheDocument();
    } finally {
      capabilities.pending = false;
    }
  });

  it("selects modules from the runtime inventory while preserving unavailable selections", async () => {
    const onChange = vi.fn();
    render(
      <SchemaValue
        label="Disabled modules"
        schema={{ type: "array", items: { type: "string" }, "x-options": { source: "modules" } }}
        value={["missing"]}
        disabled={false}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("checkbox", { name: /missing.*unavailable/i })).toBeChecked();
    await userEvent.setup().click(screen.getByRole("checkbox", { name: /maple/ }));
    expect(onChange).toHaveBeenCalledWith(["missing", "maple"]);
  });
});

describe("Safe generic setting edits", () => {
  it("does not submit a masked secret on focus, blur or Enter without a replacement", async () => {
    render(
      <Row
        setting={{
          ...setting("user"),
          key: "token",
          value: "**********",
          json_schema: { type: "string", format: "password" },
        }}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Token"));
    await user.keyboard("{Enter}{Tab}");
    expect(mutate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("stages a replacement secret and writes it only after restart-and-apply confirmation", async () => {
    render(
      <Row
        setting={{
          ...setting("user"),
          key: "token",
          value: "**********",
          json_schema: { type: "string", writeOnly: true },
        }}
      />,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Token"), "example-replacement");
    await user.tab();
    expect(mutateAsync).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mutateAsync).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /Restart and apply: Token/ }));
    expect(mutateAsync).toHaveBeenCalledExactlyOnceWith({ key: "sample.token", value: '"example-replacement"' });
    expect(screen.getByLabelText("Token")).toHaveValue("");
  });

  it("never includes a staged secret in the loaded-settings verification expectation", async () => {
    render(
      <Row
        setting={{
          ...setting("user"),
          key: "token",
          value: null,
          default: null,
          json_schema: { anyOf: [{ type: "string", format: "password", writeOnly: true }, { type: "null" }] },
        }}
      />,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Token"), "new-secret-value");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByTestId("expected-settings")).toHaveTextContent("[]");
    expect(screen.getByTestId("expected-settings")).not.toHaveTextContent("new-secret-value");
  });

  it("keeps environment-owned values read-only with no Reset", () => {
    render(<Row setting={setting("env")} />);
    expect(screen.getByLabelText("Limit")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
  });

  it("shows the declared numeric unit beside its editor", () => {
    render(<Row setting={{ ...setting("default"), json_schema: { type: "number", "x-unit": "days" } }} />);
    expect(screen.getByText("days")).toBeInTheDocument();
  });

  it("preserves a branch default when the nullable wrapper does not declare one", () => {
    expect(
      unwrap({ anyOf: [{ type: "integer", minimum: 1, default: 2 }, { type: "null" }], type: "object", minimum: -1 }),
    ).toMatchObject({ type: "integer", minimum: 1, default: 2 });
  });
});

describe("Declared duration and path formats", () => {
  it("edits the canonical duration amount and unit without converting it", async () => {
    const onChange = vi.fn();
    render(
      <SchemaValue
        label="Offline after"
        schema={{ type: "string", format: "duration" }}
        value="36h"
        disabled={false}
        onChange={onChange}
      />,
    );
    const user = userEvent.setup();
    expect(screen.getByRole("textbox", { name: "Offline after amount" })).toHaveValue("36");
    await user.selectOptions(screen.getByRole("combobox", { name: "Offline after unit" }), "m");
    expect(onChange).toHaveBeenCalledWith("36m");
  });

  it("does not save a fractional or zero duration amount", async () => {
    const onChange = vi.fn();
    render(
      <SchemaValue
        label="Interval"
        schema={{ type: "string", format: "duration" }}
        value="2d"
        disabled={false}
        onChange={onChange}
      />,
    );
    const user = userEvent.setup();
    const amount = screen.getByRole("textbox", { name: "Interval amount" });
    await user.clear(amount);
    await user.type(amount, "0");
    await user.tab();
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("positive whole number");
  });

  it("edits path arrays as individual entries", async () => {
    const onChange = vi.fn();
    render(
      <SchemaValue
        label="Folders"
        schema={{ type: "array", items: { type: "string", format: "path" } }}
        value={["/tmp/sample"]}
        disabled={false}
        onChange={onChange}
      />,
    );
    await userEvent.setup().type(screen.getByRole("textbox", { name: "Add to Folders" }), "/tmp/other{Enter}");
    expect(onChange).toHaveBeenCalledWith(["/tmp/sample", "/tmp/other"]);
  });
});

it("stages registered preview switches and preserves unknown configured values", async () => {
  const user = userEvent.setup();
  render(
    <Row
      setting={{
        ...setting("user"),
        section: "hz",
        key: "features",
        value: ["old_flag"],
        json_schema: {
          type: "array",
          title: "Preview features",
          "x-feature-catalog": { connected_history: "Private history preview" },
        },
      }}
    />,
  );
  expect(screen.queryByRole("switch", { name: "Old flag" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("switch", { name: "Connected history" }));
  expect(screen.getByTestId("expected-settings")).toHaveTextContent('"value":["old_flag","connected_history"]');
  await user.click(screen.getByRole("button", { name: "Restart and apply: Preview features" }));
  expect(mutateAsync).toHaveBeenCalledWith({ key: "hz.features", value: '["old_flag", "connected_history"]' });
});

it("disables preview switches overridden by the environment", () => {
  render(
    <Row
      setting={{
        ...setting("env"),
        section: "hz",
        key: "features",
        value: [],
        json_schema: {
          type: "array",
          "x-feature-catalog": { connected_history: "Private history preview" },
        },
      }}
    />,
  );
  expect(screen.getByRole("switch", { name: "Connected history" })).toBeDisabled();
});

it.each(["project", "explicit", "env"])(
  "prevents applying staged preview flags after a %s override",
  async (source) => {
    const user = userEvent.setup();
    const features = {
      ...setting("user"),
      section: "hz",
      key: "features",
      value: [],
      json_schema: {
        type: "array",
        title: "Preview features",
        "x-feature-catalog": { connected_history: "Private history preview" },
      },
    };
    const { rerender } = render(<Row setting={features} />);
    await user.click(screen.getByRole("switch", { name: "Connected history" }));
    expect(screen.getByRole("button", { name: "Restart and apply: Preview features" })).toBeVisible();

    rerender(<Row setting={{ ...features, source }} />);
    expect(screen.getByRole("switch", { name: "Connected history" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Restart and apply: Preview features" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  },
);
