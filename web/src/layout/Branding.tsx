// A theme's branding (ADR 0044), the one component the sidebar and the theme previews both draw, so they can't drift:
// the theme's wordmark for the mode shown (its square mark in a collapsed rail), else the product lockup: the swarm
// mark (docs/BRANDING.md: the swarm is the logo, the bust is for one Heinzel) and the app's name as live text, in the
// theme's font, a pixel above the navigation's size and a weight heavier, so the header reads as the brand and not
// as another row; the name truncates where the mark never does.
import type { SchemaThemeView } from "@/api/client";
import { HeinzelSwarm } from "@/components/HeinzelSwarm";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

type Props = { theme: SchemaThemeView | undefined; dark: boolean; mark?: boolean; className?: string };

/** The name beside the mark: the theme's UI size plus one pixel, semibold, a touch tighter; whole in a 256 px column. */
const WORDMARK_TEXT = "truncate text-[length:calc(var(--text-ui)+1px)] leading-none font-semibold tracking-[-0.01em]";

export function Branding({ theme, dark, mark = false, className }: Props) {
  // The built-in theme brings no logo of its own to show: it is the Heinzelmännchen's.
  const logo = theme && theme.name !== "default" ? theme.logo : null;
  const src = logo ? (mark ? logo.mark : dark ? logo.dark : logo.light) : null;
  if (!logo || !src) {
    return mark ? (
      <HeinzelSwarm className={className} />
    ) : (
      <span data-testid="heinzel-wordmark" className={cn("flex min-w-0 items-center gap-2", className)}>
        <HeinzelSwarm />
        <span className={WORDMARK_TEXT}>{t("app.name")}</span>
      </span>
    );
  }
  // An <img>, never inline SVG: a script in the file can't run, and the web head serves it under a strict CSP.
  return (
    <img
      src={src}
      alt={logo.alt}
      style={mark ? undefined : { height: logo.height }}
      className={cn(mark ? "object-contain" : "w-auto max-w-44 object-contain object-left", className)}
    />
  );
}
