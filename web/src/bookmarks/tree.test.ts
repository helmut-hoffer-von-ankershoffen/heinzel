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
  siblings,
  stepPlace,
  topLevel,
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
});
