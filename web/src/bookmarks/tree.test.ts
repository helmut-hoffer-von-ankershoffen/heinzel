import { describe, expect, it } from "vitest";
import type { SchemaBookmark, SchemaBookmarkList } from "@/api/client";
import {
  bookmarkAddress,
  bookmarkOf,
  dropPlace,
  entries,
  folders,
  inside,
  originOf,
  shownEntries,
  siblings,
  stepPlace,
  topLevel,
  treeStep,
} from "./tree";

const entry = (id: string, kind: "bookmark" | "folder", parent: string | null, depth: number): SchemaBookmark => ({
  id,
  kind,
  title: id.toUpperCase(),
  url: kind === "bookmark" ? `https://example.com/${id}` : null,
  origin: kind === "bookmark" ? "https://example.com" : null,
  parent,
  depth,
});
// a, F[ b, G[ c ] ], d
const list: SchemaBookmarkList = {
  bookmarks: [
    entry("a", "bookmark", null, 0),
    entry("f", "folder", null, 0),
    entry("b", "bookmark", "f", 1),
    entry("g", "folder", "f", 1),
    entry("c", "bookmark", "g", 2),
    entry("d", "bookmark", null, 0),
  ],
};
const by = (id: string) => entries(list).find((item) => item.id === id) as SchemaBookmark;

describe("the bookmark tree", () => {
  it("lists only top-level bookmarks for the navigation: no folder and nothing inside one", () => {
    expect(topLevel(list).map((item) => item.id)).toEqual(["a", "d"]);
    expect(topLevel(undefined)).toEqual([]);
    expect(folders(list).map((item) => item.id)).toEqual(["f", "g"]);
    expect(siblings(list, "f").map((item) => item.id)).toEqual(["b", "g"]);
    expect(siblings(list, null).map((item) => item.id)).toEqual(["a", "f", "d"]);
  });

  it("finds the bookmark of a page by its exact address", () => {
    expect(bookmarkOf(list, "https://example.com/c")?.id).toBe("c");
    expect(bookmarkOf(list, "https://example.com/")).toBeUndefined();
    expect(bookmarkOf(list, undefined)).toBeUndefined();
  });

  it("names origins as hz does, and none for anything but HTTPS", () => {
    expect(originOf("https://Example.com:8443/x?y")).toBe("https://example.com:8443");
    expect(originOf("http://example.com/")).toBeNull();
    expect(originOf("about:blank")).toBeNull();
    expect(originOf(undefined)).toBeNull();
  });

  it("knows what is inside a folder", () => {
    expect(inside(list, "c", "f")).toBe(true);
    expect(inside(list, "f", "f")).toBe(true);
    expect(inside(list, "a", "f")).toBe(false);
  });

  it("drops into a folder, before a bookmark, or at the end of the top level", () => {
    expect(dropPlace(list, "a", by("f"))).toEqual({ parent: "f", before: null });
    expect(dropPlace(list, "a", by("c"))).toEqual({ parent: "g", before: "c" });
    expect(dropPlace(list, "c", by("a"))).toEqual({ parent: null, before: "a" });
    expect(dropPlace(list, "c", null)).toEqual({ parent: null, before: null });
  });

  it("refuses a drop on itself and of a folder into its own subtree", () => {
    expect(dropPlace(list, "a", by("a"))).toBeNull();
    expect(dropPlace(list, "f", by("g"))).toBeNull();
    expect(dropPlace(list, "f", by("c"))).toBeNull();
    expect(dropPlace(list, "f", by("b"))).toBeNull();
  });

  it("steps an entry up and down among its siblings only", () => {
    expect(stepPlace(list, by("a"), -1)).toBeNull();
    expect(stepPlace(list, by("a"), 1)).toEqual({ parent: null, before: "d" });
    expect(stepPlace(list, by("f"), 1)).toEqual({ parent: null, before: null });
    expect(stepPlace(list, by("d"), 1)).toBeNull();
    expect(stepPlace(list, by("d"), -1)).toEqual({ parent: null, before: "f" });
    expect(stepPlace(list, by("g"), -1)).toEqual({ parent: "f", before: "b" });
    expect(stepPlace(list, entry("x", "bookmark", null, 0), 1)).toBeNull();
  });

  it("reads a typed address as HTTPS with a real host, and nothing else", () => {
    expect(bookmarkAddress(" example.com/docs ")).toBe("https://example.com/docs");
    expect(bookmarkAddress("https://example.com")).toBe("https://example.com/");
    for (const refused of ["", "two words", "localhost", "http://example.com", "https://u:p@example.com", "https://"])
      expect(bookmarkAddress(refused)).toBeNull();
  });

  it("shows what is not inside a closed folder", () => {
    const ids = (open: string[]) => shownEntries(list, new Set(open)).map((item) => item.id);
    expect(ids([])).toEqual(["a", "f", "d"]);
    expect(ids(["f"])).toEqual(["a", "f", "b", "g", "d"]);
    expect(ids(["g"])).toEqual(["a", "f", "d"]); // open, but inside a closed one
    expect(ids(["f", "g"])).toEqual(["a", "f", "b", "g", "c", "d"]);
    expect(shownEntries(undefined, new Set())).toEqual([]);
  });

  it("steps through the shown tree like a tree view", () => {
    const open = new Set(["f"]);
    const shown = shownEntries(list, open);
    const step = (current: string, key: Parameters<typeof treeStep>[3]) => treeStep(shown, open, current, key);
    expect(step("a", "ArrowDown")).toEqual({ focus: "f" });
    expect(step("a", "ArrowUp")).toEqual({ focus: undefined });
    expect(step("d", "ArrowDown")).toEqual({ focus: undefined });
    expect(step("b", "Home")).toEqual({ focus: "a" });
    expect(step("b", "End")).toEqual({ focus: "d" });
    expect(step("f", "ArrowRight")).toEqual({ focus: "b" }); // open: into it
    expect(step("g", "ArrowRight")).toEqual({ open: "g" }); // closed: open it
    expect(step("b", "ArrowRight")).toEqual({});
    expect(step("f", "ArrowLeft")).toEqual({ close: "f" });
    expect(step("g", "ArrowLeft")).toEqual({ focus: "f" }); // closed: to its folder
    expect(step("b", "ArrowLeft")).toEqual({ focus: "f" });
    expect(step("a", "ArrowLeft")).toEqual({});
    expect(step("nowhere", "ArrowDown")).toEqual({});
    // An open folder with nothing in it has nowhere to go.
    const empty = { bookmarks: [by("f")] };
    expect(treeStep(shownEntries(empty, open), open, "f", "ArrowRight")).toEqual({});
  });
});
