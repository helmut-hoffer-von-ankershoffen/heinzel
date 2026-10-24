// Made-up answers of the API, shaped by the generated schema so they drift with it.
import type {
  SchemaConfigReport,
  SchemaContractGraph,
  SchemaContractList,
  SchemaExpertMode,
  SchemaHealthReport,
  SchemaProfile,
  SchemaTeamShow,
} from "@/api/client";

export const doctor: SchemaHealthReport = {
  status: "degraded",
  checks: {
    "system.doctor": [
      { name: "python", status: "up", detail: "3.14.0", fix: "" },
      { name: "uv", status: "degraded", detail: "uv is not on PATH", fix: "Install uv: https://docs.astral.sh/uv/" },
    ],
    org: [{ name: "profile", status: "degraded", detail: "missing org.handle", fix: "Run `hz setup`" }],
  },
};

export const config: SchemaConfigReport = {
  files: [{ scope: "user", path: "/home/mira/.config/hz/hz.toml", exists: true }],
  settings: [
    {
      section: "hz",
      key: "log_level",
      value: "INFO",
      default: "WARNING",
      source: "user",
      env: "HZ_LOG_LEVEL",
      description: "How much to log.",
    },
    {
      section: "org",
      key: "role",
      value: null,
      default: null,
      source: "default",
      env: "HZ_ORG__ROLE",
      description: "Your role.",
    },
    {
      section: "system.runner",
      key: "ask_max_usd",
      value: 0.25,
      default: 0.25,
      source: "default",
      env: "HZ_SYSTEM_RUNNER__ASK_MAX_USD",
      description: "Most one answer may cost.",
    },
  ],
};

export const profile: SchemaProfile = {
  handle: "mira",
  role: "engineering-manager",
  timezone: "Europe/Berlin",
  working_hours: "09-18",
  working_hours_set: false,
  github: "mira-example",
  coding_agent: "claude-code",
  name: "Mira Example",
  email: "mira@example.com",
  complete: true,
  missing: [],
  fix: "hz setup",
};

export const contracts: SchemaContractList = {
  directories: [],
  contracts: [
    {
      name: "weekly-digest",
      namespace: "me",
      title: "Weekly digest",
      file: "/home/mira/workflows/weekly-digest.prose.md",
      schedule: "0 9 * * MON",
      scheduled: true,
      valid: true,
      errors: 0,
      warnings: 0,
    },
  ],
  next: [],
};

export const graph: SchemaContractGraph = {
  nodes: [
    { id: "tracker.issues(project=ACME)", kind: "source", maintained: true },
    { id: "me:weekly-digest", kind: "contract", maintained: true },
    { id: "contract:me/weekly-digest#digest", kind: "asset", maintained: true },
  ],
  edges: [
    { source: "tracker.issues(project=ACME)", target: "me:weekly-digest", kind: "reads", input: "issues" },
    { source: "me:weekly-digest", target: "contract:me/weekly-digest#digest", kind: "maintains", input: null },
  ],
  cycles: [],
  unmaintained: [],
  mermaid: "",
  next: [],
};

export const noTeam: SchemaTeamShow = {
  repository: null,
  checkout: null,
  exists: false,
  name: null,
  plugin: null,
  members: [],
  problems: [],
  next: ["Join the team you were invited to: hz team join <url>", "Or create one: hz team new <name>"],
};

/** Every read the shell makes, so a screen renders without unanswered calls. */
const audiences: SchemaExpertMode["operations"] = [
  { module: "system.doctor", operation: "doctor", audience: "everyday" },
  { module: "team", operation: "show", audience: "everyday" },
  { module: "workflow", operation: "list", audience: "everyday" },
  { module: "workflow", operation: "graph", audience: "expert" },
  { module: "workflow", operation: "playbooks", audience: "expert" },
  { module: "workflow", operation: "discover", audience: "expert" },
  { module: "system.capability", operation: "list", audience: "expert" },
];

/** `hz system expert` by default: the everyday screens only. */
export const everyday: SchemaExpertMode = { expert: false, operations: audiences, next: ["hz system expert on"] };
/** `hz system expert` after `hz system expert on`: every screen. */
export const expert: SchemaExpertMode = { expert: true, operations: audiences, next: ["hz system expert off"] };

export const shell = {
  "/api/system/expert": everyday,
  "/api/workflow/list": contracts,
  "/api/org/show": profile,
  "/api/system/config/show": config,
  "/api/workflow/playbooks": { playbooks: [], next: [] },
};
