// The navigation tree: sections and their screens. Contracts add one entry each, from `hz workflow list`.
// A screen names the operation it shows; outside expert mode only screens of everyday operations are listed, the
// same split `hz --help` makes (ADR 0041). A screen that isn't listed still opens by its URL.
import type { SchemaExpertMode } from "@/api/client";
import type { MessageKey } from "@/i18n";

/** An operation as `hz system expert` names it: its module's qualified name and its own, e.g. `workflow:graph`. */
export type OperationKey = `${string}:${string}`;
export type NavItem = { to: string; label: MessageKey; operation?: OperationKey };
export type NavSection = { label: MessageKey; to?: string; operation?: OperationKey; items: NavItem[] };

export const CONTRACTS_SECTION: MessageKey = "nav.contracts";

export const sections: NavSection[] = [
  { label: "nav.home", to: "/", items: [] },
  { label: "nav.settings", to: "/settings", items: [] },
  { label: CONTRACTS_SECTION, items: [{ to: "/contracts", label: "nav.graph", operation: "workflow:graph" }] },
  {
    label: "nav.organisation",
    items: [
      // No operation reads the org chart yet; it points at the capability bindings, which are for experts.
      { to: "/org", label: "nav.orgChart", operation: "system.capability:list" },
      { to: "/team", label: "nav.team", operation: "team:show" },
    ],
  },
  {
    label: "nav.inventory",
    items: [
      { to: "/inventory/types", label: "nav.assetTypes", operation: "workflow:playbooks" },
      { to: "/inventory/assets", label: "nav.assets", operation: "workflow:discover" },
    ],
  },
  { label: "nav.doctor", to: "/doctor", operation: "system.doctor:doctor", items: [] },
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
