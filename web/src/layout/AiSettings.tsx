import type { SchemaSettingInfo } from "@/api/client";
import { Row } from "./SettingControls";

const GROUPS = {
  usage: [
    "Usage meter",
    "All providers that report usage.",
    "The daily budget is a meter target. It never stops a run. Unreported costs are not included.",
  ],
  budget: [
    "Answer budget",
    "Runner answers and chats.",
    "Codex cannot enforce a dollar cap. Custom commands must enforce the budget themselves. Personal Heinzels also have their own policy limits.",
  ],
  chat: [
    "Chat history and tools",
    "Chats stored by Heinzel, across providers.",
    "Personal Heinzel conversations have separate policy and retention rules. Their history is not removed by these chat retention settings.",
  ],
  model: [
    "Claude Code and custom command model",
    "Only these two runners use this default.",
    "Leave this empty for Claude subscription chat: a model override blocks its access check and personal chat. A custom command uses it only through its model placeholder. A chat's own model choice takes precedence.",
  ],
  claude: [
    "Claude Code",
    "Only the Claude Code runner.",
    "The file-tool allowlist applies to runner jobs that request file access. It does not grant tools to personal Heinzel chats.",
  ],
  codex: [
    "Codex CLI",
    "Only the Codex CLI runner.",
    "These settings do not affect Sign in with ChatGPT or the Model API.",
  ],
  command: [
    "Custom command runner",
    "Only your configured command runner.",
    "These settings also apply when a company gateway selects this runner. The command is responsible for enforcing its read-only and budget promises.",
  ],
  gateway: [
    "Company AI gateway",
    "Only the company gateway access option.",
    "The status and test commands check access. The gateway's selected runner serves chat; its settings are separate.",
  ],
  api: [
    "Model API",
    "Only the Model API access option.",
    "Model, endpoint and API format belong together. Keep the API key in Set up Model API. These settings do not affect Claude Code, Codex or plugin models.",
  ],
  chatgpt: [
    "Sign in with ChatGPT",
    "Only the ChatGPT access option.",
    "This model is used by the ChatGPT access test. Signing in does not configure a Codex CLI model or grant personal Heinzel chat access.",
  ],
  plugin: [
    "Provider models",
    "Model providers, including plugins and local test models.",
    "Model and effort are defaults for the selected provider. A chat's own choices take precedence. Effort applies only to models that support it.",
  ],
  other: ["Other AI settings", "Scope is described beside each setting.", ""],
} as const;

type Group = keyof typeof GROUPS;

function runnerSettingGroup(key: string): Group {
  if (key === "ask_max_usd") return "budget";
  if (key === "model") return "model";
  if (key.startsWith("codex_")) return "codex";
  if (key.startsWith("command")) return "command";
  if (key === "claude_executable" || key === "read_only_tools") return "claude";
  return "other";
}

function accessSettingGroup(key: string): Group {
  if (key.startsWith("gateway_")) return "gateway";
  if (key.startsWith("plugin_")) return "plugin";
  if (key === "chatgpt_model") return "chatgpt";
  if (["model", "base_url", "api"].includes(key)) return "api";
  return "other";
}

/** Keep unknown settings editable when a server adds fields before this page is updated. */
export function aiSettingGroup({ section, key }: Pick<SchemaSettingInfo, "section" | "key">): Group {
  if (section === "ai") return "usage";
  if (section === "system.runner.chat") return "chat";
  if (section === "system.runner") return runnerSettingGroup(key);
  if (section === "ai.access") return accessSettingGroup(key);
  return "other";
}

export function AiSettings({ settings }: Readonly<{ settings: SchemaSettingInfo[] }>) {
  return (
    <>
      {Object.entries(GROUPS).map(([group, [title, scope, detail]]) => {
        const rows = settings.filter((setting) => aiSettingGroup(setting) === group);
        if (!rows.length) return null;
        return (
          <section key={group} aria-label={title} className="mt-6">
            <h3 className="text-ui font-medium">{title}</h3>
            <p className="mt-0.5 text-sm text-muted-foreground">{scope}</p>
            {detail && (
              <details className="mt-1 text-sm text-muted-foreground">
                <summary className="cursor-pointer">What this affects</summary>
                <p className="mt-1">{detail}</p>
              </details>
            )}
            <ul>
              {rows.map((setting) => (
                <Row key={`${setting.section}.${setting.key}`} setting={setting} />
              ))}
            </ul>
          </section>
        );
      })}
    </>
  );
}
