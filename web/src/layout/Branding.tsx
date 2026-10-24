// A theme's branding (ADR 0044), the one component the sidebar and the theme previews both draw, so they can't drift:
// the theme's wordmark for the mode shown (its square mark in a collapsed rail), else the Heinzel and the app's name.
import type { SchemaThemeView } from "@/api/client";
import { HeinzelIcon } from "@/components/HeinzelIcon";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

type Props = { theme: SchemaThemeView | undefined; dark: boolean; mark?: boolean; className?: string };

export function Branding({ theme, dark, mark = false, className }: Props) {
  // The built-in theme brings no logo of its own to show: it is the Heinzel's.
  const logo = theme && theme.name !== "default" ? theme.logo : null;
  const src = logo ? (mark ? logo.mark : dark ? logo.dark : logo.light) : null;
  if (!logo || !src) {
    return mark ? (
      <HeinzelIcon className={cn("text-xl", className)} />
    ) : (
      <span className={cn("flex min-w-0 items-center gap-2 text-ui font-medium", className)}>
        <HeinzelIcon className="text-xl" />
        <span className="truncate">{t("app.name")}</span>
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
