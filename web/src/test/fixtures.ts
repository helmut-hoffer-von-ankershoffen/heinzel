// Made-up answers of the API, shaped by the generated schema so they drift with it.
import type {
  SchemaConfigReport,
  SchemaContractGraph,
  SchemaExpertMode,
  SchemaHealthReport,
  SchemaHeinzelList,
  SchemaProfile,
  SchemaTeamShow,
  SchemaThemeColours,
  SchemaThemeList,
  SchemaThemeView,
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
  can_reveal: true,
  file_manager: "Finder",
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

export const heinzelmaennchen: SchemaHeinzelList = {
  directories: [],
  filtered: false,
  heinzelmaennchen: [
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
      state: "scheduled",
      unmet: [],
      last_run: "2026-10-19T07:00:00Z",
      next_run: "2026-10-26T09:00:00+01:00",
      drafts: 2,
    },
    {
      name: "team-report",
      namespace: "team",
      title: "Team report",
      file: "/home/mira/team/workflows/team-report.prose.md",
      schedule: null,
      scheduled: false,
      valid: true,
      errors: 0,
      warnings: 0,
      state: "blocked",
      unmet: ["tracker:read"],
      last_run: null,
      next_run: null,
      drafts: 0,
    },
  ],
  next: [],
};
/** The sidebar and search read the same list. */
export const contracts = heinzelmaennchen;

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
  { module: "heinzel", operation: "list", audience: "everyday" },
  { module: "graph", operation: "graph", audience: "everyday" },
  { module: "heinzel", operation: "graph", audience: "expert" },
  { module: "heinzel", operation: "playbooks", audience: "expert" },
  { module: "heinzel", operation: "discover", audience: "expert" },
  { module: "system.capability", operation: "list", audience: "expert" },
];

/** `hz system expert` by default: the everyday screens only. */
export const everyday: SchemaExpertMode = { expert: false, operations: audiences, next: ["hz system expert on"] };
/** `hz system expert` after `hz system expert on`: every screen. */
export const expert: SchemaExpertMode = { expert: true, operations: audiences, next: ["hz system expert off"] };

export const shell = {
  "/api/system/expert": everyday,
  "/api/heinzel/list": contracts,
  "/api/org/show": profile,
  "/api/system/config/show": config,
  "/api/heinzel/playbooks": { playbooks: [], next: [] },
};

const colours = (overrides: Partial<SchemaThemeColours> = {}): SchemaThemeColours => ({
  accent: "#ebebeb",
  accent_foreground: "#1a1a1a",
  background: "#ffffff",
  bad: "#b4372d",
  border: "#e4e4e4",
  card: "#ffffff",
  foreground: "#1a1a1a",
  icon: "#5c5c5c",
  input: "#ffffff",
  muted: "#f2f2f2",
  muted_foreground: "#6b6b6b",
  ok: "#1f7444",
  popover: "#ffffff",
  primary: "#b4532f",
  primary_foreground: "#ffffff",
  ring: "#b4532f",
  sidebar: "#f3f3f3",
  warn: "#8a5c00",
  ...overrides,
});

/** A theme as `hz system theme list` gives it. */
export const theme = (overrides: Partial<SchemaThemeView> = {}): SchemaThemeView => ({
  name: "default",
  title: "Default",
  module: null,
  plugin: null,
  selected: false,
  light: colours(),
  dark: colours({ background: "#181818", foreground: "#ececec" }),
  font_sans: "system-ui, sans-serif",
  font_mono: "ui-monospace, monospace",
  font_size: "15px",
  font_size_small: "13px",
  icon_size: "18px",
  row_height: "36px",
  radius: "0.75rem",
  radius_control: "0.5rem",
  border_width: "1px",
  heading_weight: 600,
  heading_tracking: "0",
  spacing: "0.25rem",
  logo: null,
  ...overrides,
});

/** The made-up Acme theme: square controls, teal icons, a tint and a logo. */
export const acmeTheme = theme({
  name: "acme",
  title: "Acme",
  module: "acme",
  plugin: "heinzel-acme",
  radius: "0.25rem",
  radius_control: "0rem",
  light: colours({ icon: "#0f766e", primary: "#0f766e", main_tint: "#f7f4ee" }),
  dark: colours({ icon: "#5eead4", background: "#0b1f1d" }),
  logo: {
    light: "/theme-assets/acme/light.svg?v=1",
    dark: "/theme-assets/acme/dark.svg?v=1",
    mark: "/theme-assets/acme/mark.svg?v=1",
    alt: "Acme",
    height: "28px",
  },
});

/** `hz system theme list` with `selected` in effect. */
export const themeList = (selected: string, themes: SchemaThemeView[] = [theme(), acmeTheme]): SchemaThemeList =>
  ({
    selected,
    themes: themes.map((item) => ({ ...item, selected: item.name === selected })),
    conflicts: [],
    note: null,
    next: [],
  }) as unknown as SchemaThemeList;
