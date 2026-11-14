// The bookmarks' data: `hz system bookmark` operations. The tree is owner data in hz, never in the browser's storage.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SchemaBookmarkList, unwrap } from "@/api/client";
import type { Place } from "./tree";

export const BOOKMARKS_KEY = ["system", "bookmark", "list"] as const;

/** `hz system bookmark list`; read again now and then, since an agent or the CLI may add one. */
export const useBookmarks = () =>
  useQuery({
    queryKey: BOOKMARKS_KEY,
    queryFn: () => unwrap(api.GET("/system/bookmark/list")),
    refetchInterval: 60_000,
    // An older hz without bookmarks answers 404: no bookmarks then, and no storm of retries.
    retry: false,
  });

const place = ({ parent, before }: Partial<Place>) => ({
  ...(parent ? { parent } : {}),
  ...(before ? { before } : {}),
});

export const bookmarkCalls = {
  add: (input: { url: string; title?: string } & Partial<Place>) =>
    unwrap(
      api.POST("/system/bookmark/add", {
        params: { query: { url: input.url, ...(input.title ? { title: input.title } : {}), ...place(input) } },
      }),
    ),
  folder: (input: { title: string } & Partial<Place>) =>
    unwrap(api.POST("/system/bookmark/folder", { params: { query: { title: input.title, ...place(input) } } })),
  edit: (input: { entry: string; title?: string; url?: string }) =>
    unwrap(api.POST("/system/bookmark/edit", { params: { query: input } })),
  move: (input: { entry: string } & Place) =>
    unwrap(api.POST("/system/bookmark/move", { params: { query: { entry: input.entry, ...place(input) } } })),
  remove: (entry: string) => unwrap(api.POST("/system/bookmark/remove", { params: { query: { entry } } })),
  restore: (entry: string) => unwrap(api.POST("/system/bookmark/restore", { params: { query: { entry } } })),
  favicon: (input: { origin: string; image: string }) =>
    unwrap(api.POST("/system/bookmark/favicon", { params: { query: input } })),
};

/** Run one bookmark call; its answer is the whole new tree, shown at once everywhere. */
export function useBookmarkCall<Input>(call: (input: Input) => Promise<SchemaBookmarkList>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: call, onSuccess: (list) => client.setQueryData(BOOKMARKS_KEY, list) });
}
