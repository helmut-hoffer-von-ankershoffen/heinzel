import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SchemaSettingInfo } from "@/api/client";
import { SchemaValue } from "./SchemaValue";
import { Row } from "./SettingControls";
import { controlFor, unwrap } from "./settingSchema";

const mutate = vi.fn();
const mutateAsync = vi.fn();
vi.mock("@/api/queries", () => ({
  useSetSetting: () => ({ mutate, mutateAsync, isPending: false }),
  useCapabilities: () => ({
    data: {
      adapters: [
        { adapter: "maple", capability: "calendar", distribution: "sample-maple", module: "maple" },
        { adapter: "oak", capability: "mail", distribution: "sample-oak", module: "oak" },
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
      <button type="button" onClick={() => void onUnsupported()}>
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
      "Saved Enabled. This server cannot restart hz ui. Run `hz ui` in a terminal to apply this setting.",
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
