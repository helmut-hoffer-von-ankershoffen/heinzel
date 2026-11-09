/** The web app event the menu items raise; the page decides what each one opens. */
export const MENU_EVENT = "hz:menu";

const SITE = "https://heinzel.team";
const REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";
/** Pages the Help menu opens: the project site (mkdocs `site_url`) and the repository. Nothing else may open. */
export const HELP_URLS = Object.freeze({
  help: `${SITE}/`,
  tour: `${SITE}/guides/tour/`,
  catalog: `${SITE}/CATALOG/`,
  repository: REPOSITORY,
  start: `${SITE}/guides/install/`,
  support: `${REPOSITORY}/issues`,
  feedback: `${REPOSITORY}/issues/new`,
  privacy: `${SITE}/privacy/`,
  imprint: `${SITE}/imprint/`,
});

/** Whether a link may leave the app: https on the project site or under the project's GitHub account. */
export function isAllowedHelpUrl(target) {
  try {
    const url = new URL(target);
    if (url.protocol !== "https:" || url.username || url.password) return false;
    return url.hostname === "heinzel.team" ||
      (url.hostname === "github.com" && url.pathname.startsWith("/helmut-hoffer-von-ankershoffen/"));
  } catch {
    return false;
  }
}

/**
 * The macOS application menu, as data: About and Settings and Check for Updates open the web app's own screens
 * through `send(action)`; the rest are the standard roles. Pure, so a unit test can read it without Electron.
 */
export function appMenuTemplate({ name, send, open, devTools = false }) {
  return [
    {
      label: name,
      submenu: [
        { label: `About ${name}`, click: () => send("about") },
        { type: "separator" },
        { label: "Settings\u2026", accelerator: "Command+,", click: () => send("settings") },
        { label: "Check for Updates\u2026", click: () => send("updates") },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide", label: `Hide ${name}` },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit", label: `Quit ${name}` },
      ],
    },
    {
      label: "File",
      submenu: [
        { label: "New Chat", accelerator: "CommandOrControl+N", click: () => send("new-chat") },
        { type: "separator" },
        { role: "close" },
      ],
    },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { label: "Search\u2026", accelerator: "CommandOrControl+K", click: () => send("search") },
        { label: "Toggle Sidebar", accelerator: "CommandOrControl+B", click: () => send("sidebar") },
        { type: "separator" },
        { label: "Toggle Terminal", accelerator: "CommandOrControl+J", click: () => send("terminal") },
        { label: "Toggle Browser", accelerator: "CommandOrControl+Shift+B", click: () => send("browser") },
        { label: "Toggle Side Chat", accelerator: "CommandOrControl+;", click: () => send("chat") },
        { label: "Show Notifications", accelerator: "CommandOrControl+Shift+N", click: () => send("notifications") },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "reload" },
        ...(devTools ? [{ role: "toggleDevTools" }] : []),
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Go",
      submenu: [
        { label: "Today", accelerator: "CommandOrControl+1", click: () => send("go:today") },
        { label: `${name}`, accelerator: "CommandOrControl+2", click: () => send("go:heinzel") },
        { label: "Team", accelerator: "CommandOrControl+3", click: () => send("go:team") },
        { label: "Plugins", accelerator: "CommandOrControl+4", click: () => send("go:plugins") },
      ],
    },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        { label: `${name} Help`, click: () => open(HELP_URLS.help) },
        { label: "Getting Started", click: () => open(HELP_URLS.start) },
        { label: "Product Tour", click: () => open(HELP_URLS.tour) },
        { label: "In-App Help", click: () => send("help") },
        { label: "Plugin Catalog", click: () => open(HELP_URLS.catalog) },
        { label: "View Release Notes", click: () => send("changelog") },
        {
          label: "Troubleshooting",
          submenu: [
            { label: "Run Doctor", click: () => send("doctor") },
            { label: "Copy Diagnostics", click: () => send("copy") },
            { label: "Restart Daemon\u2026", click: () => send("daemon") },
          ],
        },
        { type: "separator" },
        { label: "Get Support", click: () => open(HELP_URLS.support) },
        { label: "Report an Issue\u2026", click: () => open(HELP_URLS.feedback) },
        { label: `${name} on GitHub`, click: () => open(HELP_URLS.repository) },
        { type: "separator" },
        { label: "Privacy Policy", click: () => open(HELP_URLS.privacy) },
        { label: "Imprint", click: () => open(HELP_URLS.imprint) },
      ],
    },
  ];
}

export const ACTIONS = [
  "about", "settings", "updates", "changelog", "doctor", "copy", "daemon",
  "search", "sidebar", "terminal", "browser", "chat", "new-chat", "notifications", "help",
  "go:today", "go:heinzel", "go:team", "go:plugins",
];

/** The script that tells the page which menu item was chosen; the action is one of a fixed few. */
export function menuScript(action) {
  if (!ACTIONS.includes(action)) throw new Error("Unknown menu action.");
  return `window.dispatchEvent(new CustomEvent(${JSON.stringify(MENU_EVENT)}, { detail: ${JSON.stringify(action)} }))`;
}

/** One structured log line per menu action: stable event name, fields, no data from the page. */
export function menuLogLine(action, outcome) {
  return JSON.stringify({ event: "desktop.menu.action", action, outcome });
}

/** The structured line for a Help link: the host only, never the whole URL. */
export function helpLogLine(url, allowed) {
  let host = "invalid";
  try {
    host = new URL(url).hostname;
  } catch {
    // an unparsable link is logged as blocked with an invalid host
  }
  return JSON.stringify({ event: "desktop.help.open", target_host: host, outcome: allowed ? "opened" : "blocked" });
}
