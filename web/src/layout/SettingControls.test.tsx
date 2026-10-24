// Every kind of control a setting's schema picks, and the rules of docs/SETTINGS_UX.md over every real setting
// (src/test/fixtures/settings.json, which `make docs` writes from the kernel and the first-party plugins).
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import type { SchemaSettingInfo } from "@/api/client";
import { shell } from "@/test/fixtures";
import every from "@/test/fixtures/settings.json";
import { expectAccessible, mockApi } from "@/test/render";
import { Row, UNDO_MS } from "./SettingControls";
import { controlFor, type JsonSchema, labelOf, listed, typedThoughKnown, unwrap } from "./settingSchema";

const answers = {
  ...shell,
  "/api/system/config/set": "/home/mira/.config/hz/config.toml",
  "/api/system/config/unset": "/home/mira/.config/hz/config.toml",
  "/api/system/info": { version: "0.5.0", modules: [{ qualname: "clock" }, { qualname: "github" }] },
  "/api/system/capability/list": {
    capabilities: [],
    adapters: [{ adapter: "github", capability: "tracker" }],
    uses: [],
  },
  "/api/system/config/reveal-setting": {
    key: "hz.log_dir",
    path: "/home/mira/logs",
    supported: true,
    opened: true,
    message: "Opened",
  },
};

const setting = (overrides: Partial<SchemaSettingInfo> & { json_schema?: JsonSchema }): SchemaSettingInfo =>
  ({
    section: "demo",
    key: "value",
    value: null,
    default: null,
    source: "default",
    env: "HZ_DEMO__VALUE",
    description: "What it does.",
    ...overrides,
  }) as SchemaSettingInfo;

function renderRow(info: SchemaSettingInfo, extra: Record<string, unknown> = {}) {
  const { calls } = mockApi({
    ...answers,
    "/api/system/config/show": { ...shell["/api/system/config/show"], can_reveal: true, file_manager: "Finder" },
    ...extra,
  });
  sessionStorage.setItem("hz.token", "test-token");
  const view = render(
    <Providers client={queryClient()}>
      <ul>
        <Row setting={info} />
      </ul>
    </Providers>,
  );
  return { ...view, calls };
}

/** The `value` of the last `config set`, or "unset" for `config unset`. */
const lastWrite = (calls: Request[]) => {
  const write = calls.filter((call) => /\/system\/config\/(set|unset)/.test(call.url)).at(-1);
  if (!write) return undefined;
  return write.url.includes("/unset") ? "unset" : new URL(write.url).searchParams.get("value");
};

describe("Every setting there is", () => {
  const settings = every as unknown as SchemaSettingInfo[];

  it("has a control of its own whenever its values are known: never a field to type into", () => {
    const typed = settings
      .filter((s) => typedThoughKnown(s.json_schema as JsonSchema | null))
      .map((s) => `${s.section}.${s.key}`);
    expect(typed).toEqual([]);
  });

  it("shows plain help: no backticks, ADR numbers or TOML in what Settings lists", () => {
    const markup = /`|\bADR\b|\[[a-z_]+(\.[a-z_]+)*\]|\{ *[a-z_]+ *=|= *\[/;
    const shown = settings.filter((s) => listed(s.json_schema as JsonSchema | null, true));
    expect(shown.filter((s) => markup.test(s.description)).map((s) => `${s.section}.${s.key}`)).toEqual([]);
    expect(shown.filter((s) => /\b(Dir|Usd|Ui|Api)\b/.test(labelOf(s.key, s.json_schema as JsonSchema))).length).toBe(
      0,
    );
  });

  it("lists expert settings only in expert mode, and never a deprecated or hidden one", () => {
    const schemas = settings.map((s) => s.json_schema as JsonSchema | null);
    const everyday = schemas.filter((schema) => listed(schema, false)).length;
    const all = schemas.filter((schema) => listed(schema, true)).length;
    expect(everyday).toBeLessThan(all);
    expect(all).toBeLessThan(schemas.length);
  });
});

describe("The control a schema picks", () => {
  it("offers choices by their labels", async () => {
    const schema = { type: "string", enum: ["text", "json"], "x-labels": { text: "Text", json: "JSON" } };
    const { calls, container } = renderRow(setting({ key: "output_format", value: "text", json_schema: schema }));
    const select = screen.getByRole("combobox", { name: "Output format" });
    expect(within(select).getByRole("option", { name: "JSON" })).toBeInTheDocument();
    await userEvent.setup().selectOptions(select, "json");
    await waitFor(() => expect(lastWrite(calls)).toBe('"json"'));
    await expectAccessible(container);
  });

  it("reads hours of a day in the clock of their zone: am and pm in New York, 24 hours in Berlin", () => {
    const hours = {
      type: "array",
      "x-unit": "hour-of-day",
      prefixItems: [
        { type: "integer", minimum: 0, maximum: 24 },
        { type: "integer", minimum: 0, maximum: 24 },
      ],
    };
    expect(controlFor(hours)).toMatchObject({ kind: "range", hours: true });
    const zones = {
      type: "array",
      items: {
        type: "object",
        required: ["name"],
        properties: { name: { type: "string", format: "iana-timezone" }, hours },
      },
    };
    renderRow(
      setting({
        key: "zones",
        value: [
          { name: "America/New_York", hours: [9, 18] },
          { name: "Europe/Berlin", hours: [9, 18] },
        ],
        json_schema: zones,
      }),
    );
    expect(screen.getByRole("combobox", { name: "hours (New York) from" })).toHaveDisplayValue("9:00 am");
    expect(screen.getByRole("combobox", { name: "hours (New York) to" })).toHaveDisplayValue("6:00 pm");
    expect(screen.getByRole("combobox", { name: "hours (Berlin) to" })).toHaveDisplayValue("18:00");
  });

  it("shows a number with its unit, and says what fits when it doesn't", async () => {
    const schema = { type: "number", exclusiveMinimum: 0, maximum: 600, "x-unit": "seconds" };
    const { calls } = renderRow(setting({ key: "timeout", value: 60, json_schema: schema }));
    expect(screen.getByText("seconds")).toBeInTheDocument();
    const user = userEvent.setup();
    const field = screen.getByRole("spinbutton", { name: "Timeout" });
    await user.clear(field);
    await user.type(field, "900{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a number more than 0. At most 600.");
    await user.clear(field);
    await user.type(field, "0{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("more than 0");
    expect(lastWrite(calls)).toBeUndefined();
    await user.clear(field);
    await user.type(field, "30{Enter}");
    await waitFor(() => expect(lastWrite(calls)).toBe("30"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("puts a dollar sign before an amount of money", () => {
    renderRow(setting({ key: "ask_max_usd", value: 0.25, json_schema: { type: "number", "x-unit": "usd" } }));
    expect(screen.getByText("$")).toBeInTheDocument();
  });

  it("takes a duration as a number and a unit, and writes it as 36h", async () => {
    const { calls } = renderRow(
      setting({ key: "offline_after", value: "2d", json_schema: { type: "string", format: "duration" } }),
    );
    expect(screen.getByRole("spinbutton", { name: "Offline after" })).toHaveValue(2);
    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Unit of Offline after" }), "h");
    await waitFor(() => expect(lastWrite(calls)).toBe('"2h"'));
  });

  it("shows a path with Show in Finder, which opens it through hz", async () => {
    const { calls } = renderRow(
      setting({
        section: "hz",
        key: "log_dir",
        value: "/home/mira/logs",
        json_schema: { type: "string", format: "path" },
      }),
    );
    await userEvent.setup().click(await screen.findByRole("button", { name: "Show in Finder: Log dir" }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/system/config/reveal-setting"))).toBe(true));
    const reveal = calls.find((call) => call.url.includes("reveal-setting"));
    expect(new URL(reveal?.url ?? "").searchParams.get("key")).toBe("hz.log_dir");
  });

  it("never shows a secret or a box to type one into, only whether it is set and how to set it", () => {
    const schema = { anyOf: [{ type: "string", format: "password", writeOnly: true }, { type: "null" }] };
    renderRow(
      setting({ key: "github_token", value: "**********", source: "user", env: "HZ_X__TOKEN", json_schema: schema }),
    );
    expect(screen.getByText("Set")).toBeInTheDocument();
    expect(screen.getByText(/set it in the environment as HZ_X__TOKEN/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    // Only a mask reaches the page: Reset or Undo would put the mask in place of the secret.
    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
  });

  it("names the end of the day midnight where 24 would read as the 12:00 am it starts with", () => {
    const schema = {
      type: "array",
      "x-unit": "hour-of-day",
      prefixItems: [
        { type: "integer", minimum: 0, maximum: 24 },
        { type: "integer", minimum: 0, maximum: 24 },
      ],
    };
    const zones = {
      type: "array",
      items: {
        type: "object",
        required: ["name"],
        properties: { name: { type: "string", format: "iana-timezone" }, hours: schema },
      },
    };
    renderRow(setting({ key: "zones", value: [{ name: "America/New_York", hours: [0, 24] }], json_schema: zones }));
    expect(screen.getByRole("combobox", { name: "hours (New York) to" })).toHaveDisplayValue("12:00 am (midnight)");
    expect(screen.getByRole("combobox", { name: "hours (New York) from" })).toHaveDisplayValue("12:00 am");
  });

  it("checks a web address as it is entered", async () => {
    const schema = { type: "string", format: "uri", examples: ["https://example.atlassian.net"] };
    const { calls } = renderRow(setting({ key: "site_url", json_schema: schema }));
    const field = screen.getByRole("textbox", { name: "Site URL" });
    expect(field).toHaveAttribute("placeholder", "https://example.atlassian.net");
    const user = userEvent.setup();
    await user.type(field, "example{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a web address starting with https://");
    expect(lastWrite(calls)).toBeUndefined();
    await user.clear(field);
    await user.type(field, "http://localhost:11434{Enter}");
    await waitFor(() => expect(lastWrite(calls)).toBe('"http://localhost:11434"'));
  });

  it("offers a list's choices from its own source: a capability's adapters, not modules", async () => {
    const schema = {
      type: "array",
      items: { type: "string" },
      "x-options": { source: "adapters", capability: "tracker" },
    };
    renderRow(setting({ key: "trackers", value: [], json_schema: schema }));
    expect(await screen.findByRole("checkbox", { name: "github" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "clock" })).not.toBeInTheDocument();
  });

  it("picks switched-off modules from the installed ones", async () => {
    const schema = { type: "array", items: { type: "string" }, "x-options": { source: "modules" } };
    const { calls } = renderRow(setting({ key: "disabled_modules", value: [], json_schema: schema }));
    await userEvent.setup().click(await screen.findByRole("checkbox", { name: "clock" }));
    await waitFor(() => expect(lastWrite(calls)).toBe('["clock"]'));
  });

  it("picks the adapter of a capability from the installed ones, automatic by default", async () => {
    const schema = {
      anyOf: [{ type: "string" }, { type: "null" }],
      "x-options": { source: "adapters", capability: "tracker", none: "none" },
    };
    const { calls } = renderRow(setting({ section: "capabilities", key: "tracker", json_schema: schema }));
    const select = screen.getByRole("combobox", { name: "Tracker" });
    expect(select).toHaveDisplayValue("Automatic");
    await within(select).findByRole("option", { name: "github" });
    await userEvent.setup().selectOptions(select, "github");
    await waitFor(() => expect(lastWrite(calls)).toBe('"github"'));
  });

  it("labels a setting by its title, else by its key in words", () => {
    expect(labelOf("ui_port", { title: "Ui Port" })).toBe("UI port");
    expect(labelOf("log_dir", { title: "Log folder" })).toBe("Log folder");
    expect(unwrap({ anyOf: [{ type: "string" }, { type: "null" }], title: "T", "x-expert": true })).toMatchObject({
      type: "string",
      title: "T",
      "x-expert": true,
    });
  });
});

describe("Changing a setting", () => {
  it("names the default it resets to, and Reset unsets it", async () => {
    const schema = { type: "string", enum: ["text", "json"], "x-labels": { text: "Text", json: "JSON" } };
    const { calls } = renderRow(
      setting({ key: "output_format", value: "json", default: "text", source: "user", json_schema: schema }),
    );
    expect(screen.getByText("Default: Text")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(lastWrite(calls)).toBe("unset"));
  });

  it("undoes a change of a setting that was at its default by unsetting it", async () => {
    const schema = { type: "string", enum: ["text", "json"] };
    const { calls } = renderRow(setting({ key: "output_format", default: "text", json_schema: schema }));
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "Output format" }), "json");
    await waitFor(() => expect(lastWrite(calls)).toBe('"json"'));
    await user.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(lastWrite(calls)).toBe("unset"));
  });

  it("undoes a Reset by writing the value back", async () => {
    const schema = { type: "string", enum: ["text", "json"] };
    const { calls } = renderRow(
      setting({ key: "output_format", value: "json", default: "text", source: "user", json_schema: schema }),
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Output format is back to its default.");
    await user.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(lastWrite(calls)).toBe('"json"'));
  });

  it("offers Undo for a while after a change, which puts the value before back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const schema = { type: "string", enum: ["text", "json"] };
      const { calls } = renderRow(
        setting({ key: "output_format", value: "text", source: "user", json_schema: schema }),
      );
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.selectOptions(screen.getByRole("combobox", { name: "Output format" }), "json");
      const undo = await screen.findByRole("button", { name: "Undo" });
      await user.click(undo);
      await waitFor(() => expect(lastWrite(calls)).toBe('"text"'));
      await user.selectOptions(screen.getByRole("combobox", { name: "Output format" }), "json");
      await screen.findByRole("button", { name: "Undo" });
      await act(async () => {
        vi.advanceTimersByTime(UNDO_MS + 100);
      });
      expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says why the server refused a value, under the setting", async () => {
    const { calls } = renderRow(setting({ key: "handle", json_schema: { type: "string" } }), {
      "/api/system/config/set": { status: 400, body: { error: "'Mira' is not a handle: lowercase letters" } },
    });
    await userEvent.setup().type(screen.getByRole("textbox", { name: "Handle" }), "Mira{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("is not a handle: lowercase letters");
    expect(lastWrite(calls)).toBe('"Mira"');
  });
});
