// The bookmark manager: `hz system bookmark` as a tree. Folders, rename, drag to reorder and move, remove with undo.
import { ArrowDown, ArrowUp, Folder, FolderPlus, GripVertical, Pencil, Plus, Trash2 } from "lucide-react";
import { type FormEvent, type ReactNode, useState } from "react";
import type { SchemaBookmark, SchemaBookmarkList } from "@/api/client";
import { Favicon } from "@/bookmarks/Favicon";
import { openInApp, opensInApp } from "@/bookmarks/open";
import { bookmarkCalls, useBookmarkCall, useBookmarks } from "@/bookmarks/queries";
import { bookmarkAddress, dropPlace, entries, folders, inside, stepPlace } from "@/bookmarks/tree";
import { Loaded, Page } from "@/components/common";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

const FIELD = "min-w-0 rounded-lg border border-border bg-background px-2 py-1 text-sm";

export function Bookmarks() {
  const bookmarks = useBookmarks();
  return (
    <Page title={t("bookmarks.title")} intro={t("bookmarks.intro")}>
      <Loaded query={bookmarks}>{(list) => <Manager list={list} />}</Loaded>
    </Page>
  );
}

function Manager({ list }: Readonly<{ list: SchemaBookmarkList }>) {
  const add = useBookmarkCall(bookmarkCalls.add);
  const folder = useBookmarkCall(bookmarkCalls.folder);
  const edit = useBookmarkCall(bookmarkCalls.edit);
  const move = useBookmarkCall(bookmarkCalls.move);
  const remove = useBookmarkCall(bookmarkCalls.remove);
  const restore = useBookmarkCall(bookmarkCalls.restore);
  const [address, setAddress] = useState("");
  const [folderName, setFolderName] = useState("");
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [dragged, setDragged] = useState<string | null>(null);
  const [removed, setRemoved] = useState<{ id: string; title: string } | null>(null);
  const failure = [add, folder, edit, move, remove, restore].find((call) => call.error)?.error;
  const inApp = opensInApp();

  const addBookmark = (event: FormEvent) => {
    event.preventDefault();
    const url = bookmarkAddress(address);
    if (url) add.mutate({ url }, { onSuccess: () => setAddress("") });
  };
  const addFolder = (event: FormEvent) => {
    event.preventDefault();
    if (folderName.trim()) folder.mutate({ title: folderName.trim() }, { onSuccess: () => setFolderName("") });
  };
  const rename = (event: FormEvent) => {
    event.preventDefault();
    if (!renaming) return;
    if (renaming.title.trim()) edit.mutate({ entry: renaming.id, title: renaming.title.trim() });
    setRenaming(null);
  };
  const drop = (target: SchemaBookmark | null) => {
    const place = dragged ? dropPlace(list, dragged, target) : null;
    if (dragged && place) move.mutate({ entry: dragged, ...place });
    setDragged(null);
  };
  const droppable = (target: SchemaBookmark | null) => dragged !== null && dropPlace(list, dragged, target) !== null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-4">
        <form onSubmit={addBookmark} className="flex min-w-0 flex-1 items-center gap-2">
          <input
            aria-label={t("bookmarks.addAddress")}
            placeholder="https://"
            type="text"
            inputMode="url"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            className={`${FIELD} flex-1`}
          />
          <Button type="submit" variant="outline" disabled={!bookmarkAddress(address) || add.isPending}>
            <Plus aria-hidden />
            {t("bookmarks.add")}
          </Button>
        </form>
        <form onSubmit={addFolder} className="flex min-w-0 flex-1 items-center gap-2">
          <input
            aria-label={t("bookmarks.folderName")}
            placeholder={t("bookmarks.folderName")}
            type="text"
            maxLength={200}
            value={folderName}
            onChange={(event) => setFolderName(event.target.value)}
            className={`${FIELD} flex-1`}
          />
          <Button type="submit" variant="outline" disabled={!folderName.trim() || folder.isPending}>
            <FolderPlus aria-hidden />
            {t("bookmarks.newFolder")}
          </Button>
        </form>
      </div>
      {failure && (
        <p role="alert" className="rounded-md border border-bad/40 p-2 text-sm">
          {failure.message || t("bookmarks.failed")}
        </p>
      )}
      {removed && list.restorable?.includes(removed.id) && (
        <p role="status" className="flex items-center gap-2 rounded-md border border-border p-2 text-sm">
          <span className="min-w-0 flex-1 truncate">{t("bookmarks.removed", { title: removed.title })}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => restore.mutate(removed.id, { onSuccess: () => setRemoved(null) })}
          >
            {t("bookmarks.undo")}
          </Button>
        </p>
      )}
      {entries(list).length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("bookmarks.empty")}</p>
      ) : (
        <ul aria-label={t("bookmarks.tree")} className="flex flex-col rounded-lg border border-border">
          {entries(list).map((entry) => {
            const up = stepPlace(list, entry, -1);
            const down = stepPlace(list, entry, 1);
            return (
              <li
                key={entry.id}
                draggable
                data-entry={entry.id}
                data-drop={droppable(entry) ? "yes" : undefined}
                onDragStart={(event) => {
                  event.dataTransfer?.setData("text/plain", entry.id);
                  setDragged(entry.id);
                }}
                onDragEnd={() => setDragged(null)}
                onDragOver={(event) => {
                  if (droppable(entry)) event.preventDefault();
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  drop(entry);
                }}
                className="flex flex-wrap items-center gap-2 border-b-[0.5px] border-foreground/10 py-1 pr-2 last:border-b-0 data-[drop=yes]:bg-accent/40"
                style={{ paddingLeft: `${0.5 + (entry.depth ?? 0) * 1.25}rem` }}
              >
                <GripVertical aria-hidden className="size-4 shrink-0 cursor-grab text-muted-foreground" />
                {entry.kind === "folder" ? (
                  <Folder aria-hidden className="size-4 shrink-0" />
                ) : (
                  <Favicon image={entry.origin ? list.favicons?.[entry.origin] : null} />
                )}
                {renaming?.id === entry.id ? (
                  <form onSubmit={rename} className="flex min-w-0 flex-1 items-center gap-2">
                    <input
                      aria-label={t("bookmarks.name")}
                      // biome-ignore lint/a11y/noAutofocus: the person just asked to rename this entry
                      autoFocus
                      type="text"
                      maxLength={200}
                      value={renaming.title}
                      onChange={(event) => setRenaming({ id: entry.id, title: event.target.value })}
                      className={`${FIELD} flex-1`}
                    />
                    <Button type="submit" variant="outline" size="sm">
                      {t("bookmarks.save")}
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setRenaming(null)}>
                      {t("bookmarks.cancel")}
                    </Button>
                  </form>
                ) : entry.kind === "folder" ? (
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{entry.title}</span>
                ) : (
                  <a
                    href={entry.url ?? "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={entry.url ?? undefined}
                    className="min-w-0 flex-1 truncate text-sm hover:underline"
                    onClick={(event) => {
                      if (!inApp || !entry.url) return;
                      event.preventDefault();
                      void openInApp(entry.url);
                    }}
                  >
                    {entry.title}
                  </a>
                )}
                <select
                  aria-label={t("bookmarks.moveTo", { title: entry.title })}
                  value={entry.parent ?? ""}
                  onChange={(event) =>
                    move.mutate({ entry: entry.id, parent: event.target.value || null, before: null })
                  }
                  className="max-w-32 rounded-lg border border-border bg-background px-1 py-1 text-xs"
                >
                  <option value="">{t("bookmarks.topLevel")}</option>
                  {folders(list)
                    .filter((target) => !inside(list, target.id, entry.id))
                    .map((target) => (
                      <option key={target.id} value={target.id}>
                        {`${"  ".repeat(target.depth ?? 0)}${target.title}`}
                      </option>
                    ))}
                </select>
                <RowButton
                  label={t("bookmarks.moveUp", { title: entry.title })}
                  disabled={!up}
                  onClick={() => up && move.mutate({ entry: entry.id, ...up })}
                >
                  <ArrowUp aria-hidden />
                </RowButton>
                <RowButton
                  label={t("bookmarks.moveDown", { title: entry.title })}
                  disabled={!down}
                  onClick={() => down && move.mutate({ entry: entry.id, ...down })}
                >
                  <ArrowDown aria-hidden />
                </RowButton>
                <RowButton
                  label={t("bookmarks.rename", { title: entry.title })}
                  onClick={() => setRenaming({ id: entry.id, title: entry.title })}
                >
                  <Pencil aria-hidden />
                </RowButton>
                <RowButton
                  label={t("bookmarks.removeEntry", { title: entry.title })}
                  onClick={() =>
                    remove.mutate(entry.id, { onSuccess: () => setRemoved({ id: entry.id, title: entry.title }) })
                  }
                >
                  <Trash2 aria-hidden />
                </RowButton>
              </li>
            );
          })}
          {dragged !== null && (
            <li
              data-entry="top"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                drop(null);
              }}
              className="p-2 text-center text-xs text-muted-foreground"
            >
              {t("bookmarks.dropTop")}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

function RowButton({
  label,
  disabled,
  onClick,
  children,
}: Readonly<{ label: string; disabled?: boolean; onClick: () => void; children: ReactNode }>) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}
