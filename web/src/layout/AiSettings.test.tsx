import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import type { SchemaSettingInfo } from "@/api/client";
import { config, expert, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const fields: [string, string, string][] = [
  ["ai", "daily_budget_usd", "Usage meter"],
  ["system.runner", "ask_max_usd", "Answer budget"],
  ["system.runner", "model", "Claude Code and custom command model"],
  ["system.runner", "claude_executable", "Claude Code"],
  ["system.runner", "read_only_tools", "Claude Code"],
  ...["codex_executable", "codex_model", "codex_effort"].map((key): [string, string, string] => [
    "system.runner",
    key,
    "Codex CLI",
  ]),
  ...[
    "command",
    "command_output",
    "command_format",
    "command_draft_key",
    "command_enforces_read_only",
    "command_read_only_tools",
    "command_env",
  ].map((key): [string, string, string] => ["system.runner", key, "Custom command runner"]),
  ...[
    "gateway_title",
    "gateway_summary",
    "gateway_status_command",
    "gateway_test_command",
    "gateway_sign_in",
    "gateway_runner",
    "gateway_hosts",
    "gateway_models",
    "gateway_env",
  ].map((key): [string, string, string] => ["ai.access", key, "Company AI gateway"]),
  ...["model", "base_url", "api"].map((key): [string, string, string] => ["ai.access", key, "Model API"]),
  ["ai.access", "chatgpt_model", "Sign in with ChatGPT"],
  ["ai.access", "plugin_model", "Provider models"],
  ["ai.access", "plugin_effort", "Provider models"],
  ...["session_max_usd", "keep_days", "keep_sessions", "tools"].map((key): [string, string, string] => [
    "system.runner.chat",
    key,
    "Chat history and tools",
  ]),
  ["system.runner", "future_runner_option", "Other AI settings"],
  ["ai.access", "future_access_option", "Other AI settings"],
  ["ai.future", "future_section_option", "Other AI settings"],
];

const settings: SchemaSettingInfo[] = fields.map(([section, key]) => ({
  section,
  key,
  value: "example",
  default: null,
  source: "default",
  env: "FIXTURE",
  description: "Fixture setting.",
  json_schema: { type: "string", title: `${section}.${key}`, "x-live": true },
}));

it("keeps every AI setting once, under the provider or feature that uses it", async () => {
  mockApi({ ...shell, "/api/system/expert": expert, "/api/system/config/show": { ...config, settings } });
  const { container } = renderAt("/doctor?settings=ai");
  const dialog = await screen.findByRole("dialog", { name: "Settings" });
  await within(dialog).findByRole("region", { name: "Usage meter" });
  for (const [section, key, group] of fields) {
    const region = within(dialog).getByRole("region", { name: group });
    expect(within(region).getByLabelText(`${section}.${key}`)).toHaveValue("example");
    expect(within(dialog).getAllByLabelText(`${section}.${key}`)).toHaveLength(1);
  }
  expect(within(dialog).getByText(/including plugins and local test models/)).toBeInTheDocument();
  expect(within(dialog).getByText(/Codex cannot enforce a dollar cap/)).toBeInTheDocument();
  expect(within(dialog).getByText(/Leave this empty for Claude subscription chat/)).toBeInTheDocument();
  await expectAccessible(container);
});

it("saves to the original provider key and keeps search available", async () => {
  const { calls } = mockApi({
    ...shell,
    "/api/system/config/show": { ...config, settings },
    "/api/system/config/set": "config.toml",
  });
  const user = userEvent.setup();
  renderAt("/doctor?settings=ai");
  const field = await screen.findByLabelText("ai.access.plugin_model");
  await user.clear(field);
  await user.type(field, "example-model{Enter}");
  const call = calls.find((request) => request.url.includes("/system/config/set"));
  expect(new URL(call?.url ?? "").searchParams.get("key")).toBe("ai.access.plugin_model");
  expect(new URL(call?.url ?? "").searchParams.get("value")).toBe("example-model");
  await user.type(screen.getByRole("searchbox", { name: "Filter settings" }), "ai.access.plugin_model");
  expect(await screen.findByLabelText("ai.access.plugin_model")).toBeInTheDocument();
  expect(screen.queryByLabelText("system.runner.codex_model")).not.toBeInTheDocument();
});

it("does not expose expert settings in everyday mode", async () => {
  mockApi({
    ...shell,
    "/api/system/config/show": {
      ...config,
      settings: settings.map((setting) => ({
        ...setting,
        json_schema: { ...(setting.json_schema as object), "x-expert": setting.section === "ai.access" },
      })),
    },
  });
  renderAt("/doctor?settings=ai");
  expect(await screen.findByLabelText("ai.daily_budget_usd")).toBeInTheDocument();
  expect(screen.queryByLabelText("ai.access.plugin_model")).not.toBeInTheDocument();
});
