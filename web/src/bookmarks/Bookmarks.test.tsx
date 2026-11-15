import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { MENU_EVENT } from "@/about/menu";
import type { SchemaBookmarkList } from "@/api/client";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { BookmarkNavigation } from "./BookmarkNavigation";
import { BookmarkBar } from "./BookmarkStar";
import { Favicon } from "./Favicon";

const ICON = "data:image/png;base64,iVBORw0KGgo=";
const tree = (): SchemaBookmarkList => ({
  bookmarks: [
    {
      id: "b-1",
      kind: "bookmark",
      title: "Docs",
      url: "https://docs.example/",
      origin: "https://docs.example",
      parent: null,
      depth: 0,
    },
    { id: "b-2", kind: "folder", title: "Work", url: null, origin: null, parent: null, depth: 0 },
    {
      id: "b-3",
      kind: "bookmark",
      title: "Tracker",
      url: "https://tracker.example/",
      origin: "https://tracker.example",
      parent: "b-2",
      depth: 1,
    },
    {
      id: "b-4",
      kind: "bookmark",
      title: "News",
      url: "https://news.example/",
      origin: "https://news.example",
      parent: null,
      depth: 0,
    },
  ],
  favicons: { "https://docs.example": ICON },
  restorable: [],
});

/** The bookmark API over one tree; every write answers the tree, as hz does, and is recorded with its query. */
function bookmarkApi(start: SchemaBookmarkList = tree(), extra: Record<string, unknown> = {}) {
  let current = start;
  const writes: [string, Record<string, string>][] = [];
  const write = (
    name: string,
    change: (list: SchemaBookmarkList, query: Record<string, string>) => SchemaBookmarkList,
  ) =>
    [
      `/api/system/bookmark/${name}`,
      (request: Request) => {
        const query = Object.fromEntries(new URL(request.url).searchParams);
        writes.push([name, query]);
        current = change(current, query);
        return current;
      },
    ] as const;
  const api = mockApi({
    ...shell,
    "/api/system/bookmark/list": () => current,
    ...Object.fromEntries([
      write("add", (list, { url = "", title }) => ({
        ...list,
        bookmarks: [
          ...(list.bookmarks ?? []),
          {
            id: "b-9",
            kind: "bookmark",
            title: title ?? "new.example",
            url,
            origin: new URL(url).origin,
            parent: null,
            depth: 0,
          },
        ],
      })),
      write("folder", (list, { title = "" }) => ({
        ...list,
        bookmarks: [
          ...(list.bookmarks ?? []),
          { id: "b-8", kind: "folder", title, url: null, origin: null, parent: null, depth: 0 },
        ],
      })),
      write("edit", (list, { entry, title = "" }) => ({
        ...list,
        bookmarks: (list.bookmarks ?? []).map((item) => (item.id === entry ? { ...item, title } : item)),
      })),
      write("move", (list) => list),
      write("remove", (list, { entry = "" }) => ({
        ...list,
        bookmarks: (list.bookmarks ?? []).filter((item) => item.id !== entry),
        restorable: [entry],
      })),
      write("restore", () => tree()),
      write("favicon", (list, { origin = "", image = "" }) => ({
        ...list,
        favicons: { ...list.favicons, [origin]: image },
      })),
    ]),
    ...extra,
  });
  return { ...api, writes };
}

afterEach(() => {
  Reflect.deleteProperty(window, "heinzelDesktop");
});

describe("the navigation's bookmarks", () => {
  const openTree = async () => {
    const view = renderAt("/");
    const tree = await screen.findByRole("tree", { name: "Bookmarks" });
    const items = () => within(tree).getAllByRole("treeitem");
    return { ...view, tree, items, names: () => items().map((item) => item.textContent) };
  };
  afterEach(() => localStorage.removeItem("hz.bookmarks.open"));

  it("shows the whole tree: folders closed at first, bookmarks with favicon and title at every level", async () => {
    bookmarkApi();
    const { container, items, names } = await openTree();
    expect(names()).toEqual(["Docs", "Work", "News"]);
    expect(items()[0]?.querySelector('img[data-favicon="image"]')).toHaveAttribute("src", ICON);
    expect(items()[2]?.querySelector('[data-favicon="fallback"]')).not.toBeNull();
    const work = screen.getByRole("treeitem", { name: "Work" });
    expect(work).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(work);
    expect(work).toHaveAttribute("aria-expanded", "true");
    expect(names()).toEqual(["Docs", "Work", "Tracker", "News"]);
    const tracker = screen.getByRole("treeitem", { name: "Tracker" });
    expect(tracker).toHaveAttribute("aria-level", "2");
    expect(tracker).toHaveAttribute("title", "https://tracker.example/");
    expect(tracker.querySelector('[data-favicon="fallback"]')).not.toBeNull();
    // Chrome has no text links: every row is a button.
    expect(
      within(screen.getByRole("navigation", { name: "Main" })).queryByRole("link", { name: /Docs|Tracker/ }),
    ).toBeNull();
    await expectAccessible(container);
  });

  it("remembers which folders are open", async () => {
    bookmarkApi();
    const first = await openTree();
    fireEvent.click(screen.getByRole("treeitem", { name: "Work" }));
    expect(JSON.parse(localStorage.getItem("hz.bookmarks.open") ?? "[]")).toEqual(["b-2"]);
    first.unmount();
    const { names } = await openTree();
    expect(names()).toEqual(["Docs", "Work", "Tracker", "News"]);
    fireEvent.click(screen.getByRole("treeitem", { name: "Work" }));
    expect(localStorage.getItem("hz.bookmarks.open")).toBe("[]");
  });

  it("ignores a damaged memory of open folders", async () => {
    localStorage.setItem("hz.bookmarks.open", "{not json");
    bookmarkApi();
    expect((await openTree()).names()).toEqual(["Docs", "Work", "News"]);
  });

  it("moves with the arrow keys, opens and folds with right and left, and keeps one tab stop", async () => {
    bookmarkApi();
    const user = userEvent.setup();
    const { items, names } = await openTree();
    expect(items().map((item) => item.tabIndex)).toEqual([0, -1, -1]);
    const focused = () => waitFor(() => expect(document.activeElement).toHaveAttribute("role", "treeitem"));
    const at = (name: string) => waitFor(() => expect(document.activeElement).toHaveTextContent(name));
    (items()[0] as HTMLElement).focus();
    await focused();
    await user.keyboard("{ArrowDown}");
    await at("Work");
    expect(screen.getByRole("treeitem", { name: "Work" }).tabIndex).toBe(0);
    expect(screen.getByRole("treeitem", { name: "Docs" }).tabIndex).toBe(-1);
    await user.keyboard("{ArrowRight}"); // opens the folder
    await waitFor(() => expect(names()).toEqual(["Docs", "Work", "Tracker", "News"]));
    await at("Work");
    await user.keyboard("{ArrowRight}"); // into it
    await at("Tracker");
    await user.keyboard("{ArrowRight}"); // a bookmark has nothing to open
    await at("Tracker");
    await user.keyboard("{ArrowLeft}"); // back to its folder
    await at("Work");
    await user.keyboard("{ArrowLeft}"); // folds it
    await waitFor(() => expect(names()).toEqual(["Docs", "Work", "News"]));
    await user.keyboard("{End}");
    await at("News");
    await user.keyboard("{ArrowDown}"); // nothing below
    await at("News");
    await user.keyboard("{Home}");
    await at("Docs");
    await user.keyboard("{ArrowUp}");
    await at("Docs");
    await user.keyboard("{ArrowDown}{Enter}"); // Enter on a folder toggles it
    await waitFor(() => expect(names()).toEqual(["Docs", "Work", "Tracker", "News"]));
  });

  it("opens a bookmark in a new browser tab outside the desktop app", async () => {
    bookmarkApi();
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    await openTree();
    fireEvent.click(screen.getByRole("treeitem", { name: "Docs" }));
    expect(opened).toHaveBeenCalledExactlyOnceWith("https://docs.example/", "_blank", "noopener,noreferrer");
    opened.mockRestore();
  });

  it("opens the manager from the section's title", async () => {
    bookmarkApi();
    const user = userEvent.setup();
    await openTree();
    await user.click(screen.getByTitle("Manage bookmarks"));
    expect(await screen.findByRole("list", { name: "Bookmarks and folders" })).toBeVisible();
  });

  it("opens a bookmark in a tab of the built-in browser in the desktop app and shows the panel", async () => {
    bookmarkApi();
    const browser = vi.fn(async () => ({ status: "empty" }));
    Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const shown = vi.fn();
    const listener = (event: Event) => shown((event as CustomEvent<string>).detail);
    window.addEventListener(MENU_EVENT, listener);
    await openTree();
    fireEvent.click(screen.getByRole("treeitem", { name: "Work" }));
    fireEvent.click(screen.getByRole("treeitem", { name: "Tracker" }));
    expect(opened).not.toHaveBeenCalled(); // no second window
    opened.mockRestore();
    await waitFor(() => expect(shown).toHaveBeenCalledWith("browser-show"));
    expect(browser).toHaveBeenCalledWith({ action: "new-tab", url: "https://tracker.example/" });
    window.removeEventListener(MENU_EVENT, listener);
  });

  it("still shows the panel when every tab is in use", async () => {
    bookmarkApi();
    const browser = vi.fn(async ({ action }: Record<string, unknown>) => {
      if (action === "new-tab") throw new Error("Too many tabs.");
      return { status: "empty" };
    });
    Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
    const shown = vi.fn();
    const listener = (event: Event) => shown((event as CustomEvent<string>).detail);
    window.addEventListener(MENU_EVENT, listener);
    await openTree();
    fireEvent.click(screen.getByRole("treeitem", { name: "Docs" }));
    await waitFor(() => expect(shown).toHaveBeenCalledWith("browser-show"));
    window.removeEventListener(MENU_EVENT, listener);
  });

  it("scrolls a long tree inside its own section", async () => {
    bookmarkApi();
    const { tree } = await openTree();
    expect(tree.className).toContain("max-h-64");
    expect(tree.className).toContain("overflow-y-auto");
  });

  it("keeps the top-level bookmarks as icons in the collapsed rail", async () => {
    bookmarkApi();
    render(
      <Providers client={queryClient()}>
        <MemoryRouter>
          <BookmarkNavigation compact linkClass="rail" />
        </MemoryRouter>
      </Providers>,
    );
    const list = await screen.findByRole("list", { name: "Bookmarks" });
    expect(
      within(list)
        .getAllByRole("button")
        .map((item) => item.getAttribute("aria-label")),
    ).toEqual(["Docs", "News"]);
    expect(screen.queryByRole("tree")).toBeNull();
  });

  it("shows no tree when hz has no bookmarks", async () => {
    mockApi({ ...shell });
    renderAt("/");
    await screen.findByRole("navigation", { name: "Main" });
    expect(screen.queryByRole("tree", { name: "Bookmarks" })).toBeNull();
  });
});

describe("the bookmark manager", () => {
  const open = async () => {
    const view = renderAt("/bookmarks");
    const list = await screen.findByRole("list", { name: "Bookmarks and folders" });
    return { ...view, list, rows: () => within(list).getAllByRole("listitem") };
  };

  it("shows the tree indented by folder, with favicons and a fallback icon", async () => {
    bookmarkApi();
    const { container, rows } = await open();
    expect(rows().map((row) => row.dataset.entry)).toEqual(["b-1", "b-2", "b-3", "b-4"]);
    expect(rows()[2]?.style.paddingLeft).toBe("1.75rem");
    expect(rows()[0]?.querySelector('img[data-favicon="image"]')).toHaveAttribute("src", ICON);
    expect(rows()[2]?.querySelector('[data-favicon="fallback"]')).not.toBeNull();
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    fireEvent.click(within(rows()[2] as HTMLElement).getByRole("button", { name: "Tracker" }));
    expect(opened).toHaveBeenCalledExactlyOnceWith("https://tracker.example/", "_blank", "noopener,noreferrer");
    opened.mockRestore();
    const topBar = container.querySelector("header.workspace-topbar") as HTMLElement;
    expect(within(topBar).getByRole("button", { name: "Bookmarks" })).toBeVisible(); // it names the screen
    await expectAccessible(container);
  });

  it("adds a bookmark from a typed address and a folder by name", async () => {
    const { writes } = bookmarkApi();
    const user = userEvent.setup();
    await open();
    const add = screen.getByRole("button", { name: "Add bookmark" });
    expect(add).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "Address of the new bookmark" }), "new.example/page");
    await user.click(add);
    await waitFor(() => expect(writes).toContainEqual(["add", { url: "https://new.example/page" }]));
    await user.type(screen.getByRole("textbox", { name: "Folder name" }), "Reading");
    await user.click(screen.getByRole("button", { name: "New folder" }));
    await waitFor(() => expect(writes).toContainEqual(["folder", { title: "Reading" }]));
    expect(await screen.findByText("Reading", { selector: "li > span" })).toBeVisible();
  });

  it("renames an entry in place", async () => {
    const { writes } = bookmarkApi();
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole("button", { name: "Rename Work" }));
    const name = screen.getByRole("textbox", { name: "Name" });
    await user.clear(name);
    await user.type(name, "Office");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toContainEqual(["edit", { entry: "b-2", title: "Office" }]));
    expect(await screen.findByText("Office", { selector: "li > span" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Rename Docs" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
  });

  it("reorders with the arrow buttons and moves with the folder choice", async () => {
    const { writes } = bookmarkApi();
    const user = userEvent.setup();
    await open();
    expect(screen.getByRole("button", { name: "Move Docs up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move News down" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Move News up" }));
    await waitFor(() => expect(writes).toContainEqual(["move", { entry: "b-4", before: "b-2" }]));
    await user.click(screen.getByRole("button", { name: "Move Docs down" }));
    await waitFor(() => expect(writes).toContainEqual(["move", { entry: "b-1", before: "b-4" }]));
    await user.selectOptions(screen.getByRole("combobox", { name: "Folder of Docs" }), "Work");
    await waitFor(() => expect(writes).toContainEqual(["move", { entry: "b-1", parent: "b-2" }]));
    // A folder is never offered itself as its folder.
    expect(
      within(screen.getByRole("combobox", { name: "Folder of Work" })).queryByRole("option", { name: "Work" }),
    ).toBeNull();
  });

  it("moves by drag and drop: into a folder, before a bookmark, to the top level; never onto itself", async () => {
    const { writes } = bookmarkApi();
    const { rows, list } = await open();
    const [docs, work, tracker] = rows() as [HTMLElement, HTMLElement, HTMLElement];
    fireEvent.dragStart(docs);
    expect(fireEvent.dragOver(docs)).toBe(true); // not a place to drop
    expect(fireEvent.dragOver(work)).toBe(false);
    fireEvent.drop(work);
    await waitFor(() => expect(writes).toContainEqual(["move", { entry: "b-1", parent: "b-2" }]));
    fireEvent.dragStart(docs);
    fireEvent.drop(tracker);
    await waitFor(() => expect(writes).toContainEqual(["move", { entry: "b-1", parent: "b-2", before: "b-3" }]));
    fireEvent.dragStart(tracker);
    const top = within(list).getByText("Drop here to move it to the end of the top level");
    expect(fireEvent.dragOver(top)).toBe(false);
    fireEvent.drop(top);
    await waitFor(() => expect(writes).toContainEqual(["move", { entry: "b-3" }]));
    const before = writes.length;
    fireEvent.dragStart(docs);
    fireEvent.drop(docs);
    fireEvent.dragStart(docs);
    fireEvent.dragEnd(docs);
    expect(within(list).queryByText("Drop here to move it to the end of the top level")).toBeNull();
    expect(writes).toHaveLength(before);
  });

  it("removes with an undo that restores the entry", async () => {
    const { writes } = bookmarkApi();
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole("button", { name: "Remove News" }));
    // The shell keeps an empty live region of its own, so the note is found by its words.
    const note = (await screen.findByText("Removed “News”.")).closest<HTMLElement>('[role="status"]') as HTMLElement;
    expect(note).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove News" })).toBeNull();
    await user.click(within(note).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(writes).toContainEqual(["restore", { entry: "b-4" }]));
    expect(await screen.findByRole("button", { name: "Remove News" })).toBeVisible();
    expect(screen.queryByText("Removed “News”.")).toBeNull();
  });

  it("says so when there are none, and shows what hz answered when a change is refused", async () => {
    bookmarkApi(
      { bookmarks: [], favicons: {}, restorable: [] },
      { "/api/system/bookmark/folder": { status: 400, body: { error: "Folders nest at most 8 deep." } } },
    );
    const user = userEvent.setup();
    renderAt("/bookmarks");
    expect(await screen.findByText(/No bookmarks yet/)).toBeVisible();
    await user.type(screen.getByRole("textbox", { name: "Folder name" }), "Deep");
    await user.click(screen.getByRole("button", { name: "New folder" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Folders nest at most 8 deep.");
  });
});

function Bar(props: { url?: string; title?: string; tabs?: { url: string; favicon?: string | null }[] }) {
  const slot = document.getElementById("slot");
  return <BookmarkBar slot={slot} url={props.url} title={props.title ?? ""} tabs={props.tabs ?? []} />;
}
describe("the star in the address bar", () => {
  const show = (props: Parameters<typeof Bar>[0]) => {
    sessionStorage.setItem("hz.token", "test-token");
    const host = document.createElement("div");
    host.id = "slot";
    document.body.append(host);
    const view = render(
      <Providers client={queryClient()}>
        <MemoryRouter>
          <Bar {...props} />
        </MemoryRouter>
      </Providers>,
    );
    return { ...view, done: () => host.remove() };
  };

  it("adds the shown page with its title, then offers name, folder and remove", async () => {
    const { writes } = bookmarkApi();
    const user = userEvent.setup();
    const view = show({ url: "https://new.example/", title: "New page" });
    const star = await screen.findByRole("button", { name: "Bookmark this page" });
    expect(star).toHaveAttribute("aria-pressed", "false");
    await user.click(star);
    await waitFor(() => expect(writes).toContainEqual(["add", { url: "https://new.example/", title: "New page" }]));
    const edit = await screen.findByRole("form", { name: "Edit bookmark" });
    expect(screen.getByRole("button", { name: "Edit this bookmark" })).toHaveAttribute("aria-pressed", "true");
    const name = within(edit).getByRole("textbox", { name: "Name" });
    await waitFor(() => expect(name).toHaveValue("New page"));
    await user.clear(name);
    await user.type(name, "Mine");
    await user.selectOptions(within(edit).getByRole("combobox", { name: "Folder" }), "Work");
    await waitFor(() => expect(writes).toContainEqual(["edit", { entry: "b-9", title: "Mine" }]));
    expect(writes).toContainEqual(["move", { entry: "b-9", parent: "b-2" }]);
    expect(within(edit).getByRole("button", { name: "Manage bookmarks" })).toBeVisible();
    await user.click(within(edit).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(writes).toContainEqual(["remove", { entry: "b-9" }]));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit bookmark" })).toBeNull());
    expect(screen.getByRole("button", { name: "Bookmark this page" })).toBeVisible();
    view.done();
  });

  it("opens and closes the editor of a page that is bookmarked already", async () => {
    bookmarkApi();
    const user = userEvent.setup();
    const view = show({ url: "https://docs.example/", title: "Docs" });
    const star = await screen.findByRole("button", { name: "Edit this bookmark" });
    await user.click(star);
    await user.click(within(screen.getByRole("form", { name: "Edit bookmark" })).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("form", { name: "Edit bookmark" })).toBeNull();
    view.done();
  });

  it("stars only an HTTPS page", async () => {
    bookmarkApi();
    const view = show({ url: "about:blank" });
    expect(await screen.findByRole("button", { name: "Bookmark this page" })).toBeDisabled();
    view.done();
  });

  it("hands hz the icon a bookmarked site showed, once per icon, and none for other sites", async () => {
    const { writes } = bookmarkApi();
    const other = "data:image/png;base64,iVBORw0KGgoAAAA=";
    const view = show({
      url: "https://news.example/",
      tabs: [
        { url: "https://news.example/today", favicon: other },
        { url: "https://docs.example/", favicon: ICON }, // hz has this one already
        { url: "https://elsewhere.example/", favicon: other }, // not bookmarked
        { url: "https://tracker.example/", favicon: null },
      ],
    });
    await waitFor(() => expect(writes).toContainEqual(["favicon", { origin: "https://news.example", image: other }]));
    view.rerender(
      <Providers client={queryClient()}>
        <MemoryRouter>
          <Bar url="https://news.example/" tabs={[{ url: "https://news.example/today", favicon: other }]} />
        </MemoryRouter>
      </Providers>,
    );
    await screen.findByRole("button", { name: "Edit this bookmark" });
    expect(writes.filter(([name]) => name === "favicon")).toHaveLength(1);
    view.done();
  });
});

describe("a favicon", () => {
  it("draws only a small data image, and the fallback for anything else or a broken one", () => {
    const { container, rerender } = render(<Favicon image="https://icons.example/x.png" />);
    expect(container.querySelector('[data-favicon="fallback"]')).not.toBeNull();
    rerender(<Favicon image="data:image/svg+xml;base64,PHN2Zy8+" />);
    expect(container.querySelector("img")).toBeNull();
    rerender(<Favicon image={ICON} />);
    const image = container.querySelector("img") as HTMLImageElement;
    expect(image).toHaveAttribute("src", ICON);
    fireEvent.error(image);
    expect(container.querySelector('[data-favicon="fallback"]')).not.toBeNull();
  });
});
