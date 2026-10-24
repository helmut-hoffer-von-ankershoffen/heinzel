// Light, dark or the system's choice; the person's pick is remembered in this browser, "system" follows the OS.
import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
export type Mode = Theme | "system";
const KEY = "hz.theme";

function stored(): Mode {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

const system = (): Theme => (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
const resolve = (mode: Mode): Theme => (mode === "system" ? system() : mode);

export function applyTheme(theme: Theme = resolve(stored())): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

const CHANGED = "hz:mode";

/** The mode and a setter; every user of the hook follows a change made by another. */
export function useMode(): [Mode, (mode: Mode) => void] {
  const [mode, setMode] = useState<Mode>(stored);
  useEffect(() => {
    applyTheme(resolve(mode));
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const follow = () => mode === "system" && applyTheme(system());
    const other = () => setMode(stored());
    media?.addEventListener("change", follow);
    window.addEventListener(CHANGED, other);
    return () => {
      media?.removeEventListener("change", follow);
      window.removeEventListener(CHANGED, other);
    };
  }, [mode]);
  const set = (next: Mode) => {
    try {
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      // Not remembered: this tab only.
    }
    setMode(next);
    window.dispatchEvent(new Event(CHANGED));
  };
  return [mode, set];
}

/** Light or dark as shown now, and a toggle between them (the sidebar's sun/moon button). */
export function useTheme(): [Theme, () => void] {
  const [mode, setMode] = useMode();
  const theme = resolve(mode);
  return [theme, () => setMode(theme === "dark" ? "light" : "dark")];
}
