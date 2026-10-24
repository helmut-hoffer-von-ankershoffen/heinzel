// The sound's data (ADR 0058): `hz system sound list` and `use`, and the signals layers and muting follow.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SchemaSoundList, type SchemaSoundscapeView, unwrap } from "@/api/client";
import { useSetSetting } from "@/api/queries";
import { token } from "@/api/token";
import type { Signals } from "./machine";
import { stop } from "./store";

export const SOUND_KEY = ["system", "sound", "list"] as const;
const readList = () => unwrap(api.GET("/system/sound/list"));

/** `hz system sound list`, read again every few minutes: the part of the day changes under a playing soundscape. */
export const useSoundscapes = () => useQuery({ queryKey: SOUND_KEY, queryFn: readList, refetchInterval: 300_000 });

/** The soundscape in effect, once the list arrived; null for none. */
export function effective(list: SchemaSoundList | undefined): SchemaSoundscapeView | null {
  const found = list?.soundscapes.find((soundscape) => soundscape.name === list.effective);
  return found && found.kind !== "off" ? found : null;
}

/** The frame origin the page needs for `list`'s soundscape in effect: the stream's host, or none. */
export const frameOf = (list: SchemaSoundList | undefined): string | null =>
  effective(list)?.stream?.frame_origin ?? null;

let pageFrame: string | null | undefined;

/** Loads the page again from the network; an object, so tests can watch it. */
export const page = { reload: (): void => window.location.reload() };

/** The frame origin this page's CSP admits, as the server decided when it served the page: the first list's. */
export function rememberPageFrame(list: SchemaSoundList): void {
  if (pageFrame === undefined) pageFrame = frameOf(list);
}

/** Whether the page must load again for `list` to play: its CSP admits another frame origin than it needs. */
export const needsReload = (list: SchemaSoundList): boolean => pageFrame !== undefined && pageFrame !== frameOf(list);

/** For tests: forget the page's frame origin. */
export function resetPageFrame(): void {
  pageFrame = undefined;
}

/** `hz system sound use <name>`: stops what plays, and reloads the page when its CSP must change (ADR 0058). */
export const useSetSound = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/system/sound/use", { params: { query: { name } } })),
    onMutate: () => stop(),
    onSuccess: (list) => {
      client.setQueryData(SOUND_KEY, list);
      if (needsReload(list)) page.reload(); // the network's page, with the frame-src the new soundscape needs or none
    },
    onSettled: () => client.invalidateQueries({ queryKey: SOUND_KEY }),
  });
};

/** Save one `[system.sound]` setting: the sound follows at once, and the page reloads if its CSP must change. */
export const useSoundSetting = () => {
  const client = useQueryClient();
  const save = useSetSetting();
  return {
    isPending: save.isPending,
    save: (field: "volume" | "follow_theme" | "mute_in_meetings" | "duck_while_typing", value: number | boolean) => {
      client.setQueryData<SchemaSoundList>(SOUND_KEY, (list) => (list ? { ...list, [field]: value } : list));
      save.mutate(
        { key: `system.sound.${field}`, value: String(value) },
        {
          onSettled: async () => {
            const list = await client.fetchQuery({ queryKey: SOUND_KEY, queryFn: readList, staleTime: 0 });
            if (needsReload(list)) page.reload();
          },
        },
      );
    },
  };
};

type Agenda = { current: unknown | null; problem?: string | null };

/** Whether a meeting is in progress, from `hz org agenda` (the footer reads the same); null when nobody knows. */
export function useMeeting(enabled: boolean): boolean | null {
  const agenda = useQuery({
    queryKey: ["org", "agenda"],
    queryFn: async (): Promise<Agenda | null> => {
      const request = new Request(new URL("/api/org/agenda", window.location.origin), {
        headers: { Authorization: `Bearer ${token() ?? ""}` },
      });
      const response = await fetch(request);
      return response.ok ? ((await response.json()) as Agenda) : null;
    },
    refetchInterval: 60_000,
    retry: false,
    enabled,
  });
  if (!enabled || !agenda.data || agenda.data.problem) return null;
  return agenda.data.current !== null && agenda.data.current !== undefined;
}

/** Where signals come from (ADR 0058): today the list's (`daypart`); plugin-published `weather` joins it there. */
export interface SignalSource {
  signals(): Signals;
}

export const listSignals = (list: SchemaSoundList | undefined): SignalSource => ({
  signals: () => (list?.signals ?? {}) as Signals,
});
