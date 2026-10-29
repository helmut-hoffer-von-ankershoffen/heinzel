// Personal identities use the durable private session API; they are distinct from the existing workflow list and chat.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api, problem, type SchemaHeinzelAppearance, type SchemaSessionSnapshot } from "@/api/client";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { ACCESSORIES, AGES, BEARDS, BUILDS, HATS, SHIRTS } from "@/components/heinzel";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

const sessionsKey = ["heinzel", "sessions"] as const;
const pendingCreation = "hz.personal-heinzel.creation";
const field = "block w-full rounded border border-border bg-background px-2 py-1 text-sm";

// These private mutations do not use the API's 503-as-result convention: a non-2xx response is always uncertain.
async function personalResult<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}

function creationId(): string {
  const saved = localStorage.getItem(pendingCreation);
  if (saved) return saved;
  const id = crypto.randomUUID();
  localStorage.setItem(pendingCreation, id);
  return id;
}

export function PersonalHeinzels() {
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const creatingNow = useRef(false);
  const [creationError, setCreationError] = useState(false);
  const [locked, setLocked] = useState(false);
  const sessions = useQuery({
    queryKey: sessionsKey,
    queryFn: () => personalResult(api.GET("/heinzel/sessions")),
    retry: false,
  });
  const session = useQuery({
    queryKey: ["heinzel", "session", selected],
    queryFn: () => personalResult(api.GET("/heinzel/session", { params: { query: { session_id: selected ?? "" } } })),
    enabled: selected !== null,
    retry: false,
  });

  const create = async () => {
    if (creatingNow.current) return;
    creatingNow.current = true;
    setCreating(true);
    setCreationError(false);
    try {
      // Retain the id across reloads and unknown acknowledgements so retry never creates a second session.
      const request_id = creationId();
      const saved = await personalResult(api.POST("/heinzel/new-chat", { body: { request_id, name: "New Heinzel" } }));
      if (!/^hz-[0-9A-HJKMNP-TV-Z]{26}$/.test(saved.session_id) || saved.heinzel_id !== saved.session_id) {
        throw new Error("Invalid creation acknowledgement");
      }
      localStorage.removeItem(pendingCreation);
      client.setQueryData(["heinzel", "session", saved.session_id], saved);
      setSelected(saved.session_id);
      await client.invalidateQueries({ queryKey: sessionsKey });
    } catch {
      setCreationError(true);
    } finally {
      creatingNow.current = false;
      setCreating(false);
    }
  };

  return (
    <section aria-labelledby="personal-heinzels" className="mb-6 space-y-3 rounded-lg border border-border p-4">
      <h2 id="personal-heinzels" className="text-base font-semibold">
        {t("personal.title")}
      </h2>
      <p className="text-sm text-muted-foreground">{t("personal.intro")}</p>
      {sessions.isPending ? (
        <p role="status">{t("state.loading")}</p>
      ) : sessions.isError ? (
        <p role="alert">{t("personal.loadError")}</p>
      ) : (
        <label className="block space-y-1 text-sm">
          <span>{t("personal.choose")}</span>
          <select
            className={field}
            value={selected ?? ""}
            disabled={locked || creating}
            onChange={(event) => setSelected(event.target.value || null)}
          >
            <option value="">{t("personal.select")}</option>
            {(sessions.data.sessions ?? []).map((item) => (
              <option key={item.session_id} value={item.session_id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <Button type="button" variant="outline" disabled={creating || locked} onClick={() => void create()}>
        {creationError ? t("personal.retryCreate") : t("personal.create")}
      </Button>
      {creationError && <p role="alert">{t("personal.createError")}</p>}
      {selected && session.isPending && <p role="status">{t("state.loading")}</p>}
      {selected && session.isError && <p role="alert">{t("personal.loadError")}</p>}
      {selected && session.data && <ProfileEditor key={selected} initial={session.data} onLock={setLocked} />}
    </section>
  );
}

export function ProfileEditor({
  initial,
  onLock,
  inChat = false,
}: {
  initial: SchemaSessionSnapshot;
  onLock: (locked: boolean) => void;
  inChat?: boolean;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState(initial);
  const [name, setName] = useState(initial.name);
  const [look, setLook] = useState<SchemaHeinzelAppearance>(initial.appearance ?? { age: "grown", build: "regular" });
  const [pending, setPending] = useState<{
    request_id: string;
    name: string;
    appearance: SchemaHeinzelAppearance;
    expected_revision: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const savingNow = useRef(false);
  const [error, setError] = useState(false);

  const save = async () => {
    if (savingNow.current) return;
    savingNow.current = true;
    const request = pending ?? {
      request_id: crypto.randomUUID(),
      name,
      appearance: look,
      expected_revision: snapshot.profile_revision,
    };
    setPending(request);
    onLock(true);
    setBusy(true);
    setError(false);
    try {
      const saved = await personalResult(
        api.POST("/heinzel/profile", { body: { session_id: snapshot.session_id, ...request } }),
      );
      if (
        saved.session_id !== snapshot.session_id ||
        !Number.isInteger(saved.profile_revision) ||
        saved.profile_revision <= request.expected_revision
      ) {
        throw new Error("Invalid profile acknowledgement");
      }
      setSnapshot(saved);
      setName(saved.name);
      setLook(saved.appearance ?? { age: "grown", build: "regular" });
      setPending(null);
      onLock(false);
      client.setQueryData(["heinzel", "session", saved.session_id], saved);
      await client.invalidateQueries({ queryKey: sessionsKey });
    } catch {
      setError(true);
    } finally {
      savingNow.current = false;
      setBusy(false);
    }
  };
  const reload = async () => {
    if (savingNow.current) return;
    savingNow.current = true;
    setBusy(true);
    try {
      const saved = await personalResult(
        api.GET("/heinzel/session", { params: { query: { session_id: snapshot.session_id } } }),
      );
      if (saved.session_id !== snapshot.session_id) throw new Error("Invalid profile response");
      setSnapshot(saved);
      setName(saved.name);
      setLook(saved.appearance ?? { age: "grown", build: "regular" });
      client.setQueryData(["heinzel", "session", saved.session_id], saved);
      setPending(null);
      setError(false);
      onLock(false);
      await client.invalidateQueries({ queryKey: sessionsKey });
    } catch {
      setError(true);
    } finally {
      savingNow.current = false;
      setBusy(false);
    }
  };
  const disabled = busy || pending !== null;
  const savedLook = snapshot.appearance ?? { age: "grown" as const, build: "regular" as const };
  const hasUncommittedChanges = name !== snapshot.name || JSON.stringify(look) !== JSON.stringify(savedLook);
  useEffect(() => {
    onLock(hasUncommittedChanges || pending !== null || busy);
  }, [hasUncommittedChanges, pending, busy, onLock]);
  return (
    <form
      className="space-y-3 border-t border-border pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="flex items-center gap-4">
        <HeinzelAvatar
          label={t("personal.preview")}
          age={look.age}
          beard={look.beard ?? undefined}
          build={look.build}
          hat={look.hat ?? undefined}
          shirt={look.shirt ?? undefined}
          accessories={look.accessories}
          className="h-24 w-16"
        />
        <p className="text-sm text-muted-foreground">{t("personal.previewHelp")}</p>
      </div>
      <fieldset disabled={disabled} className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span>{t("personal.name")}</span>
          <input
            className={field}
            value={name}
            required
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.age")}</span>
          <select
            className={field}
            value={look.age}
            onChange={(event) => setLook({ ...look, age: event.target.value as SchemaHeinzelAppearance["age"] })}
          >
            {AGES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.beard")}</span>
          <select
            className={field}
            value={look.beard ?? ""}
            onChange={(event) =>
              setLook({ ...look, beard: (event.target.value || null) as SchemaHeinzelAppearance["beard"] })
            }
          >
            <option value="">{t("personal.ageDefault")}</option>
            {BEARDS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.build")}</span>
          <select
            className={field}
            value={look.build}
            onChange={(event) => setLook({ ...look, build: event.target.value as SchemaHeinzelAppearance["build"] })}
          >
            {BUILDS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.hat")}</span>
          <select
            className={field}
            value={look.hat ?? ""}
            onChange={(event) =>
              setLook({ ...look, hat: (event.target.value || null) as SchemaHeinzelAppearance["hat"] })
            }
          >
            <option value="">{t("personal.themeDefault")}</option>
            {Object.keys(HATS).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>{t("personal.shirt")}</span>
          <select
            className={field}
            value={look.shirt ?? ""}
            onChange={(event) =>
              setLook({ ...look, shirt: (event.target.value || null) as SchemaHeinzelAppearance["shirt"] })
            }
          >
            <option value="">{t("personal.themeDefault")}</option>
            {Object.keys(SHIRTS).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="space-y-1 text-sm">
          <legend>{t("personal.accessories")}</legend>
          {ACCESSORIES.map((accessory) => (
            <label key={accessory} className="mr-3 inline-flex items-center gap-1">
              <input
                type="checkbox"
                checked={(look.accessories ?? []).includes(accessory)}
                onChange={(event) =>
                  setLook({
                    ...look,
                    accessories: event.target.checked
                      ? [...(look.accessories ?? []), accessory]
                      : (look.accessories ?? []).filter((item) => item !== accessory),
                  })
                }
              />
              {accessory}
            </label>
          ))}
        </fieldset>
      </fieldset>
      {error && <p role="alert">{t("personal.saveError")}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || !name.trim()}>
          {pending ? t("personal.retrySave") : t("personal.save")}
        </Button>
        {pending && (
          <Button type="button" variant="outline" disabled={busy} onClick={() => void reload()}>
            {t("personal.reload")}
          </Button>
        )}
      </div>
      {hasUncommittedChanges && <p className="text-sm text-muted-foreground">{t("personal.saveBeforeSchedule")}</p>}
      {!inChat && (
        <Button
          type="button"
          variant="outline"
          disabled={busy || pending !== null || hasUncommittedChanges}
          onClick={() => navigate(`/heinzel?chat=${encodeURIComponent(snapshot.session_id)}`)}
        >
          {t("personal.openSchedule")}
        </Button>
      )}
    </form>
  );
}
