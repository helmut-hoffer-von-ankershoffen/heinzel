// Light or dark: the system's choice until the person picks one, which this browser remembers.
import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
const KEY = "hz.theme";

function initial(): Theme {
  const stored = localStorage.getItem(KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: Theme = initial()): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initial);
  useEffect(() => applyTheme(theme), [theme]);
  const toggle = () =>
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      localStorage.setItem(KEY, next);
      return next;
    });
  return [theme, toggle];
}
