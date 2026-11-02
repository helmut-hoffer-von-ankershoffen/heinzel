// The sound's data (ADR 0058): `hz system sound list` and `use`, and the signals layers and muting follow.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SchemaSoundList, type SchemaSoundscapeView, unwrap } from "@/api/client";
import { useSetSetting } from "@/api/queries";
import { token } from "@/api/token";
import type { Signals } from "./machine";
import { play, stop } from "./store";

export const SOUND_KEY = ["system", "sound", "list"] as const;
/** `hz system sound list`, read now. */
export const readSoundList = () => unwrap(api.GET("/system/sound/list"));

/** `hz system sound list`, read again every few minutes: the part of the day changes under a playing soundscape. */
export const useSoundscapes = () => useQuery({ queryKey: SOUND_KEY, queryFn: readSoundList, refetchInterval: 300_000 });

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

const RESUME_KEY = "hz.sound.resume";
const RESUME_WINDOW_MS = 30_000;

/** A pick that reloads the page leaves a note, so the reloaded page plays it and opens the picker again. */
function rememberResume(name: string): void {
  try {
    sessionStorage.setItem(RESUME_KEY, JSON.stringify({ name, at: Date.now() }));
  } catch {
    // no storage: the page reloads silent, and Play is one click
  }
}

/** The soundscape a pick just reloaded the page for, if the note is fresh; the note stays until `takeResume`. */
export function pendingResume(): string | null {
  try {
    const note = JSON.parse(sessionStorage.getItem(RESUME_KEY) ?? "null") as { name?: unknown; at?: unknown } | null;
    return note &&
      typeof note.name === "string" &&
      typeof note.at === "number" &&
      Date.now() - note.at < RESUME_WINDOW_MS
      ? note.name
      : null;
  } catch {
    return null;
  }
}

/** `pendingResume`, once: the note is gone afterwards. */
export function takeResume(): string | null {
  const name = pendingResume();
  try {
    sessionStorage.removeItem(RESUME_KEY);
  } catch {
    // nothing to remove
  }
  return name;
}

/** The list as it will be once `name` is picked: what the server answers, ahead of it, so the pick plays at once. */
function picked(list: SchemaSoundList, name: string): SchemaSoundList {
  return {
    ...list,
    selected: name,
    effective: name,
    follow_theme: false,
    soundscapes: list.soundscapes.map((soundscape) => ({
      ...soundscape,
      selected: soundscape.name === name,
      effective: soundscape.name === name,
    })),
  };
}

/**
 * `hz system sound use <name>`, and play it (ADR 0058): `pick` runs in the click, so the player starts inside the
 * gesture that browsers' autoplay rules need, and switches over from what played. The page reloads, when its CSP must
 * change, and plays the pick again after.
 */
export const useSetSound = () => {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/system/sound/use", { params: { query: { name } } })),
    onSuccess: (list, name) => {
      client.setQueryData(SOUND_KEY, list);
      if (needsReload(list)) {
        rememberResume(name);
        page.reload(); // the network's page, with the frame-src the new soundscape needs or none
      }
    },
    onError: () => {
      stop();
      void client.invalidateQueries({ queryKey: SOUND_KEY });
    },
    onSettled: () => client.invalidateQueries({ queryKey: SOUND_KEY }),
  });
  return {
    isPending: mutation.isPending,
    pick: (name: string) => {
      const list = client.getQueryData<SchemaSoundList>(SOUND_KEY);
      stop();
      if (!list) return mutation.mutate(name);
      const next = picked(list, name);
      void client.cancelQueries({ queryKey: SOUND_KEY }); // a read in flight must not bring the old pick back
      client.setQueryData(SOUND_KEY, next);
      if (!needsReload(next)) play(name); // otherwise the reload comes first, and the new page plays it
      mutation.mutate(name);
    },
  };
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
            try {
              const list = await client.fetchQuery({ queryKey: SOUND_KEY, queryFn: readSoundList, staleTime: 0 });
              if (needsReload(list)) page.reload();
            } catch {
              // the list can't be read now; the next poll brings it
            }
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
