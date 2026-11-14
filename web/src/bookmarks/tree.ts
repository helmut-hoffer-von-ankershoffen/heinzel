// The bookmark tree as `hz system bookmark list` gives it: a flat list, depth-first, each entry naming its folder.
import type { SchemaBookmark, SchemaBookmarkList } from "@/api/client";

/** Every entry, depth-first in order; none while the list hasn't arrived. */
export const entries = (list: SchemaBookmarkList | undefined): SchemaBookmark[] => list?.bookmarks ?? [];

/** The bookmarks (not folders) at the top level, in order: what the navigation lists. */
export const topLevel = (list: SchemaBookmarkList | undefined): SchemaBookmark[] =>
  entries(list).filter((entry) => entry.kind === "bookmark" && !entry.parent);

/** The folders, in order, for a "put it in" choice. */
export const folders = (list: SchemaBookmarkList | undefined): SchemaBookmark[] =>
  entries(list).filter((entry) => entry.kind === "folder");

/** The entries of one folder (the top level for null), in order. */
export const siblings = (list: SchemaBookmarkList, parent: string | null): SchemaBookmark[] =>
  entries(list).filter((entry) => (entry.parent ?? null) === parent);

/** The first bookmark with exactly this address, or undefined. */
export const bookmarkOf = (
  list: SchemaBookmarkList | undefined,
  url: string | undefined,
): SchemaBookmark | undefined =>
  url ? entries(list).find((entry) => entry.kind === "bookmark" && entry.url === url) : undefined;

/** `https://host[:port]` of an HTTPS address, as the bookmark list names origins; null for anything else. */
export function originOf(url: string | undefined): string | null {
  try {
    const parsed = new URL(url ?? "");
    return parsed.protocol === "https:" ? parsed.origin : null;
  } catch {
    return null;
  }
}

/** Whether `entry` is `folder` itself or inside it: a folder never moves into its own subtree. */
export function inside(list: SchemaBookmarkList, entry: string, folder: string): boolean {
  const parents = new Map(entries(list).map((item) => [item.id, item.parent ?? null]));
  for (let current: string | null = entry; current; current = parents.get(current) ?? null)
    if (current === folder) return true;
  return false;
}

export type Place = { parent: string | null; before: string | null };

/** Where a dragged entry lands when dropped on `target`: into a folder, else before a bookmark; null if refused. */
export function dropPlace(list: SchemaBookmarkList, dragged: string, target: SchemaBookmark | null): Place | null {
  if (target === null) return { parent: null, before: null };
  if (target.id === dragged) return null;
  if (target.kind === "folder") return inside(list, target.id, dragged) ? null : { parent: target.id, before: null };
  if (target.parent && inside(list, target.parent, dragged)) return null;
  return { parent: target.parent ?? null, before: target.id };
}

/** Where an entry lands one step up or down among its siblings; null at the edge. */
export function stepPlace(list: SchemaBookmarkList, entry: SchemaBookmark, step: -1 | 1): Place | null {
  const row = siblings(list, entry.parent ?? null);
  const index = row.findIndex((item) => item.id === entry.id);
  const target = index + step;
  if (index < 0 || target < 0 || target >= row.length) return null;
  // Down: before the entry after the next one, or last.
  const before = step < 0 ? row[target] : row[target + 1];
  return { parent: entry.parent ?? null, before: before?.id ?? null };
}

/** A typed address as a bookmark's: https:// is assumed, and only an HTTPS address with a dotted host counts. */
export function bookmarkAddress(value: string): string | null {
  const input = value.trim();
  if (!input || /\s/.test(input)) return null;
  const candidate = /^[a-z][a-z\d+.-]*:\/\//i.test(input) ? input : `https://${input}`;
  try {
    const url = new URL(candidate);
    return url.protocol === "https:" && url.hostname.includes(".") && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}
