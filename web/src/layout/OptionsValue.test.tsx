import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { connectorReport, themeList } from "@/test/fixtures";
import known from "@/test/fixtures/known-settings.json";
import { mockApi } from "@/test/render";
import { OptionsValue } from "./OptionsValue";
import { SchemaValue } from "./SchemaValue";
import { controlFor, type JsonSchema, type OptionsSource } from "./settingSchema";

const layer = (name: string, label: string) => ({
  name,
  label,
  parent: null,
  site: null,
  plugin: `heinzel-${name}`,
  module: name,
  adapters: [],
  roles: [],
  playbook: null,
  hint: null,
});
const module = (qualname: string) => ({
  qualname,
  summary: "",
  distribution: "heinzel",
  version: "0.5.0",
  builtin: true,
  repository: null,
  lets: {},
});

const answers = {
  "/api/system/connector/list": connectorReport(),
  "/api/system/theme/list": themeList("default"),
  "/api/system/layer/list": {
    layers: [layer("acme", "Acme"), layer("acme-payments", "Payments")],
    next: [],
    problems: [],
  },
  "/api/system/modules": [module("org"), module("team"), module("workflow")],
};

function show(from: OptionsSource, value: unknown, multiple = false) {
  const onChange = vi.fn();
  render(
    <Providers client={queryClient()}>
      <OptionsValue
        label="Setting"
        from={from}
        multiple={multiple}
        value={value}
        disabled={false}
        onChange={onChange}
      />
    </Providers>,
  );
  return onChange;
}

const offered = async () => {
  const select = (await screen.findByRole("combobox", { name: "Setting" })) as HTMLSelectElement;
  return [...select.options].map((option) => [option.value, option.textContent]);
};

describe("a choice of known values", () => {
  it("offers a capability's adapters with None, and saves the one picked", async () => {
    mockApi(answers);
    const onChange = show({ source: "adapters", capability: "tracker", none: "none" }, null);
    expect(await offered()).toEqual([
      ["", "not set"],
      ["github", "GitHub"],
      ["atlassian", "Atlassian"],
      ["none", "None"],
    ]);
    await userEvent.setup().selectOptions(screen.getByRole("combobox"), "atlassian");
    expect(onChange).toHaveBeenCalledWith("atlassian");
  });

  it("offers the themes and the layers by their titles", async () => {
    mockApi(answers);
    show({ source: "themes" }, "default");
    await waitFor(async () => expect((await offered()).map(([value]) => value)).toEqual(["", "default", "acme"]));
  });

  it("offers the layers, and keeps a value nobody offers any more", async () => {
    mockApi(answers);
    const onChange = show({ source: "layers" }, "gone");
    expect(await offered()).toEqual([
      ["", "not set"],
      ["acme", "Acme"],
      ["acme-payments", "Payments"],
      ["gone", "gone"],
    ]);
    await userEvent.setup().selectOptions(screen.getByRole("combobox"), "");
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("offers modules as boxes to tick, saving the sorted list", async () => {
    mockApi(answers);
    const onChange = show({ source: "modules" }, ["workflow"], true);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "org" }));
    expect(onChange).toHaveBeenLastCalledWith(["org", "workflow"]);
    await user.click(screen.getByRole("checkbox", { name: "workflow" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("says it is loading until the values arrive", () => {
    mockApi({});
    show({ source: "themes" }, "default");
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});

// The real schemas of the settings with known values, kept in step with the backend by
// tests/unit/modules/config_schema_test.py: each renders as a choice, never as a text field.
describe.each(Object.entries(known as Record<string, JsonSchema>))("the setting %s", (_, schema) => {
  it("is a choice", async () => {
    mockApi(answers);
    expect(controlFor(schema).kind).toBe("options");
    render(
      <Providers client={queryClient()}>
        <SchemaValue label="Setting" schema={schema} value={null} disabled={false} onChange={() => {}} />
      </Providers>,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox") ?? screen.queryAllByRole("checkbox")[0]).toBeTruthy();
  });
});
