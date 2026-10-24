// What a layout engine hands back for the team graph: each node's box, a group's members inside it with positions
// relative to the group (as React Flow wants them). `layout.ts` turns this into React Flow; the engine behind it
// (`place-elk.ts`) can be swapped without touching anything else.
import type { TeamGraph } from "./jgf";

export const NODE_WIDTH = 248;
export const NODE_HEIGHT = 64;
/** Room above a group's members for its header. */
export const GROUP_HEADER = 44;
/** Room around a group's members on the other sides. */
export const GROUP_PADDING = 16;

export type Placed = { id: string; x: number; y: number; width: number; height: number; children?: Placed[] };

/** A layout engine: the top-level boxes, groups with their members. */
export type Place = (graph: TeamGraph) => Promise<Placed[]>;
