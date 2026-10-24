// The navigation tree: sections and their screens. Contracts add one entry each, from `hz workflow list`.
import type { MessageKey } from "@/i18n";

export type NavItem = { to: string; label: MessageKey };
export type NavSection = { label: MessageKey; to?: string; items: NavItem[] };

export const sections: NavSection[] = [
  { label: "nav.home", to: "/", items: [] },
  { label: "nav.settings", to: "/settings", items: [] },
  { label: "nav.contracts", items: [{ to: "/contracts", label: "nav.graph" }] },
  {
    label: "nav.organisation",
    items: [
      { to: "/org", label: "nav.orgChart" },
      { to: "/team", label: "nav.team" },
    ],
  },
  {
    label: "nav.inventory",
    items: [
      { to: "/inventory/types", label: "nav.assetTypes" },
      { to: "/inventory/assets", label: "nav.assets" },
    ],
  },
  { label: "nav.doctor", to: "/doctor", items: [] },
];

/** The route of a contract, by its qualified name (`me:weekly-digest`). */
export const contractPath = (qualified: string) => `/contracts/${encodeURIComponent(qualified)}`;
