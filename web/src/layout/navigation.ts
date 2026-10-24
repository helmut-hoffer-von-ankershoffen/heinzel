// The navigation tree: sections and their screens. Contracts add one entry each, from `hz workflow list`.
// A screen names the operation it shows; outside expert mode only screens of everyday operations are listed, the
// same split `hz --help` makes (ADR 0041). A screen that isn't listed still opens by its URL.
import { Boxes, type LucideIcon, Network, Shapes, Share2, Users, Workflow } from "lucide-react";
import type { SchemaExpertMode } from "@/api/client";
import type { MessageKey } from "@/i18n";

/** An operation as `hz system expert` names it: its module's qualified name and its own, e.g. `workflow:graph`. */
export type OperationKey = `${string}:${string}`;
export type NavItem = { to: string; label: MessageKey; icon: LucideIcon; operation?: OperationKey };
export type NavSection = {
  label: MessageKey;
  to?: string;
  icon?: LucideIcon;
  operation?: OperationKey;
  items: NavItem[];
};

export const CONTRACTS_SECTION: MessageKey = "nav.contracts";

// The navigation holds only what people work with to get their work done. hz itself (the doctor, daemon jobs,
// plugins) lives in the account menu and in Settings; its screens still open by URL (`/doctor`).
export const sections: NavSection[] = [
  {
    label: CONTRACTS_SECTION,
    items: [
      { to: "/contracts", label: "nav.graph", icon: Workflow, operation: "workflow:graph" },
      { to: "/graph", label: "nav.teamGraph", icon: Share2, operation: "graph:graph" },
    ],
  },
  {
    label: "nav.organisation",
    items: [
      // No operation reads the org chart yet; it points at the capability bindings, which are for experts.
      { to: "/org", label: "nav.orgChart", icon: Network, operation: "system.capability:list" },
      { to: "/team", label: "nav.team", icon: Users, operation: "team:show" },
    ],
  },
  {
    label: "nav.inventory",
    items: [
      { to: "/inventory/types", label: "nav.assetTypes", icon: Shapes, operation: "workflow:playbooks" },
      { to: "/inventory/assets", label: "nav.assets", icon: Boxes, operation: "workflow:discover" },
    ],
  },
];

/** Whether the screen of `operation` is listed in `mode`; screens without one always are. Unknown mode: only those. */
export function listed(operation: OperationKey | undefined, mode: SchemaExpertMode | undefined): boolean {
  if (operation === undefined) return true;
  if (mode === undefined) return false;
  const found = mode.operations.find((item) => `${item.module}:${item.operation}` === operation);
  return found !== undefined && (mode.expert || found.audience === "everyday");
}

/** The sections and screens to list in `mode`; a section without a listed screen goes, except the contracts one. */
export function visibleSections(all: NavSection[], mode: SchemaExpertMode | undefined): NavSection[] {
  return all
    .filter((section) => listed(section.operation, mode))
    .map((section) => ({ ...section, items: section.items.filter((item) => listed(item.operation, mode)) }))
    .filter((section) => section.to || section.items.length > 0 || section.label === CONTRACTS_SECTION);
}

/** The route of a contract, by its qualified name (`me:weekly-digest`). */
export const contractPath = (qualified: string) => `/contracts/${encodeURIComponent(qualified)}`;

/** Every listed screen with an icon, flattened: the collapsed sidebar shows these as a rail, under Home. */
export const railItems = (listedSections: NavSection[]): NavItem[] =>
  listedSections.flatMap((section) =>
    section.to && section.icon ? [{ to: section.to, label: section.label, icon: section.icon }] : section.items,
  );
