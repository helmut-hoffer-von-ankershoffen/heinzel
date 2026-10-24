import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Class names, later ones winning over earlier Tailwind utilities (shadcn/ui's `cn`). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** The commands in a fix: every `backticked` span, or the command the text ends with (`Do this: hz ...`). */
export function commandsIn(text: string): string[] {
  const quoted = [...text.matchAll(/`([^`]+)`/g)].map((match) => match[1] ?? "").filter(Boolean);
  if (quoted.length) return quoted;
  const plain = /(?:^|:\s+)((?:hz|uv|make|gh|brew|claude) .+)$/.exec(text.trim());
  return plain?.[1] ? [plain[1].replace(/[.;]$/, "")] : [];
}

/** A value as `hz system config set` reads it: plain words as they are, anything else as TOML/JSON. */
export function configValue(value: unknown): string {
  if (typeof value === "string") return /^[\w./@:+-]+$/.test(value) ? value : `'${value.replaceAll("'", "'\\''")}'`;
  const json = JSON.stringify(value) ?? "";
  return /^[\w.-]+$/.test(json) ? json : `'${json.replaceAll("'", "'\\''")}'`;
}
