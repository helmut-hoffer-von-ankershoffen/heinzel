// What the team graph's lines and cards mean: one swatch per promise state, drawn as its edges are (colour and
// dashes), and one per kind of node. Never colour alone: the word says it, and the line style does for two states.
import { Bot, FileText, LayoutList } from "lucide-react";
import { t } from "@/i18n";
import { STATES } from "./jgf";
import { edgeLook } from "./layout";

const kinds = [
  { label: "teamGraph.kind.heinzel", icon: Bot },
  { label: "teamGraph.kind.artifact", icon: FileText },
  { label: "teamGraph.kind.group", icon: LayoutList },
] as const;

export function TeamLegend() {
  return (
    <figure className="flex flex-col gap-2 text-xs text-muted-foreground">
      <figcaption className="sr-only">{t("teamGraph.legend")}</figcaption>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-2" aria-label={t("teamGraph.legend.states")}>
        {STATES.map((state) => {
          const look = edgeLook(state);
          return (
            <li key={state} className="flex items-center gap-1.5">
              <svg width="28" height="10" viewBox="0 0 28 10" aria-hidden className="shrink-0">
                <line
                  x1="1"
                  y1="5"
                  x2="27"
                  y2="5"
                  stroke={look.stroke}
                  strokeOpacity={look.opacity}
                  strokeWidth="2.5"
                  strokeDasharray={look.dash}
                  strokeLinecap={state === "offline" ? "round" : "butt"}
                />
              </svg>
              {t(`teamGraph.state.${state}`)}
            </li>
          );
        })}
      </ul>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-2" aria-label={t("teamGraph.legend.kinds")}>
        {kinds.map((kind) => {
          const Icon = kind.icon;
          return (
            <li key={kind.label} className="flex items-center gap-1.5">
              <span
                className="flex size-5 items-center justify-center rounded-md border border-border bg-card"
                aria-hidden
              >
                <Icon className="size-3" />
              </span>
              {t(kind.label)}
            </li>
          );
        })}
      </ul>
    </figure>
  );
}
