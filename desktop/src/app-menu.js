/** The web app event the menu items raise; the page decides what each one opens. */
export const MENU_EVENT = "hz:menu";

const SITE = "https://heinzel.team";
const REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";
/** Pages the Help menu opens: the project site (mkdocs `site_url`) and the repository. Nothing else may open. */
export const HELP_URLS = Object.freeze({
  help: `${SITE}/`,
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
export function appMenuTemplate({ name, send, open }) {
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
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        { label: `${name} Help`, click: () => open(HELP_URLS.help) },
        { label: "Getting Started", click: () => open(HELP_URLS.start) },
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
        { label: "Send Feedback\u2026", click: () => open(HELP_URLS.feedback) },
        { type: "separator" },
        { label: "Privacy Policy", click: () => open(HELP_URLS.privacy) },
        { label: "Imprint", click: () => open(HELP_URLS.imprint) },
      ],
    },
  ];
}

const ACTIONS = ["about", "settings", "updates", "changelog", "doctor", "copy", "daemon"];

/** The script that tells the page which menu item was chosen; the action is one of a fixed few. */
export function menuScript(action) {
  if (!ACTIONS.includes(action)) throw new Error("Unknown menu action.");
  return `window.dispatchEvent(new CustomEvent(${JSON.stringify(MENU_EVENT)}, { detail: ${JSON.stringify(action)} }))`;
}
