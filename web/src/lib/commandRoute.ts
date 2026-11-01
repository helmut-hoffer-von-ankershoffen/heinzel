// Where a command's work belongs in the app. A command that sets something up opens that module's Settings page; the
// app never types commands into the terminal for the person (U3).

/** Words after `hz <module>` that mean "set it up": the work a Settings page does with fields. */
const SETUP = new Set(["setup", "set-up", "configure", "config", "settings", "location", "init"]);

/**
 * The settings section a command sets up: `hz weather setup` is "weather", `hz config set weather.location X` is
 * "weather". Undefined when the command is not about settings.
 */
export function settingsSectionFor(command: string): string | undefined {
  const words = command.trim().split(/\s+/);
  if (words[0] !== "hz" || words.length < 3) return undefined;
  if (words[1] === "config" && words[2] === "set" && words[3]) {
    const section = words[3].split("=")[0]?.replace(/^HZ_/, "").split(".").slice(0, -1).join(".");
    return section || undefined;
  }
  return SETUP.has(words[2] ?? "") && /^[a-z][\w-]*$/.test(words[1] ?? "") ? words[1] : undefined;
}
