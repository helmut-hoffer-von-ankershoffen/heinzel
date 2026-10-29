// Private skill drafts are inert. A requested scope is neither verified nor a grant.
import { useEffect, useRef, useState } from "react";
import { api, type SchemaHeinzelProposeSkillDraftInput, type SchemaSkillDraft } from "@/api/client";
import { Button } from "@/components/ui/button";
import { SkillSharingPanel } from "./SkillSharingPanel";

type DraftRequest = SchemaHeinzelProposeSkillDraftInput;
type Scope = DraftRequest["requested_scope"];
type DraftFile = { id: string; path: string; text: string };
const scopes: Scope[] = ["personal", "team", "org", "site", "company"];
const requestId = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const skillName = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const supporting =
  /^(?:scripts\/(?:[A-Za-z0-9_-]+\/){0,2}[A-Za-z0-9_-]+\.py|(?:references|templates|assets)\/(?:[A-Za-z0-9_-]+\/){0,2}[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?)$/;

function matches(pattern: RegExp, value: string): boolean {
  return pattern.exec(value)?.[0] === value;
}

function valid(request: DraftRequest, sessionId: string): boolean {
  if (
    !request ||
    request.session_id !== sessionId ||
    !matches(requestId, request.request_id) ||
    typeof request.name !== "string" ||
    !matches(skillName, request.name) ||
    request.name.length > 64 ||
    /^(?:hz|heinzel)/.test(request.name)
  )
    return false;
  if (
    !scopes.includes(request.requested_scope) ||
    (request.requested_scope === "personal"
      ? request.scope_ref != null
      : typeof request.scope_ref !== "string" || !matches(requestId, request.scope_ref))
  )
    return false;
  if (!request.files || typeof request.files !== "object" || Array.isArray(request.files)) return false;
  const entries = Object.entries(request.files);
  if (
    !entries.length ||
    entries.length > 16 ||
    !request.files["SKILL.md"] ||
    new Set(entries.map(([path]) => path.toLowerCase())).size !== entries.length
  )
    return false;
  const encoder = new TextEncoder();
  if (
    entries.some(
      ([path, text]) =>
        typeof text !== "string" ||
        (path !== "SKILL.md" && !matches(supporting, path)) ||
        path.length > 160 ||
        text.includes("\0") ||
        encoder.encode(text).length > 24 * 1024 ||
        new TextDecoder().decode(encoder.encode(text)) !== text,
    )
  )
    return false;
  return encoder.encode(JSON.stringify(request)).length < 47 * 1024;
}

function exact(saved: SchemaSkillDraft, request: DraftRequest): boolean {
  const keys = Object.keys(request.files).sort();
  return (
    saved.session_id === request.session_id &&
    saved.request_id === request.request_id &&
    saved.name === request.name &&
    saved.requested_scope === request.requested_scope &&
    (saved.scope_ref ?? null) === (request.scope_ref ?? null) &&
    saved.status === "draft" &&
    /^[a-f0-9]{64}$/.test(saved.preview_sha256) &&
    keys.length === Object.keys(saved.files).length &&
    keys.every((key) => saved.files[key] === request.files[key])
  );
}

export function SkillDraftPanel({ sessionId }: { sessionId: string }) {
  const key = `hz.skill-draft.pending.${sessionId}`;
  const lastKey = `hz.skill-draft.last.${sessionId}`;
  const [recovery] = useState(() => {
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw) return { pending: null, blocked: false };
      const candidate = JSON.parse(raw) as DraftRequest;
      return valid(candidate, sessionId) ? { pending: candidate, blocked: false } : { pending: null, blocked: true };
    } catch {
      return { pending: null, blocked: true };
    }
  });
  const [open, setOpen] = useState(recovery.pending !== null || recovery.blocked);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<Scope>("personal");
  const [scopeRef, setScopeRef] = useState("");
  const [files, setFiles] = useState<DraftFile[]>([{ id: "main", path: "SKILL.md", text: "" }]);
  const [review, setReview] = useState<DraftRequest | null>(recovery.pending);
  const [pending, setPending] = useState<DraftRequest | null>(recovery.pending);
  const [saved, setSaved] = useState<SchemaSkillDraft | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [draftRefs, setDraftRefs] = useState<string[]>([]);
  const [draftCursor, setDraftCursor] = useState<number | null>(null);
  const [lookupId, setLookupId] = useState(() => {
    try {
      const id = sessionStorage.getItem(lastKey);
      return id && matches(requestId, id) ? id : "";
    } catch {
      return "";
    }
  });
  const running = useRef(false);
  useEffect(() => {
    if (recovery.blocked)
      setError("This tab's pending draft cannot be recovered. Inspect its saved state before starting another.");
  }, [recovery.blocked]);

  const openDraft = async (selectedId = lookupId) => {
    if (running.current || !matches(requestId, selectedId)) return;
    running.current = true;
    setSaving(true);
    setError("");
    try {
      const { data, response } = await api.GET("/heinzel/skill-draft", {
        params: { query: { session_id: sessionId, request_id: selectedId } },
      });
      if (
        !response.ok ||
        !data ||
        data.session_id !== sessionId ||
        data.request_id !== selectedId ||
        data.status !== "draft" ||
        !valid(data, sessionId)
      )
        throw new Error("Unavailable");
      setSaved(data);
    } catch {
      setError("The saved draft could not be loaded.");
    } finally {
      running.current = false;
      setSaving(false);
    }
  };
  const listDrafts = async (cursor: number | null) => {
    if (running.current) return;
    running.current = true;
    setSaving(true);
    setError("");
    try {
      const { data, response } = await api.GET("/heinzel/skill-drafts", {
        params: { query: { session_id: sessionId, cursor: cursor ?? 0, limit: 20 } },
      });
      if (!response.ok || !data || data.items.some((item) => !matches(requestId, item.request_id)))
        throw new Error("Unavailable");
      setDraftRefs((old) =>
        cursor === null
          ? data.items.map((item) => item.request_id)
          : [...old, ...data.items.map((item) => item.request_id)],
      );
      setDraftCursor(data.next_cursor ?? null);
    } catch {
      setError("Saved drafts could not be loaded.");
    } finally {
      running.current = false;
      setSaving(false);
    }
  };
  const hasEdits = Boolean(
    name || scopeRef || scope !== "personal" || files.some((file) => file.text || file.path !== "SKILL.md"),
  );
  const prepare = () => {
    const entries = files.map((file) => [file.path, file.text] as const);
    if (new Set(entries.map(([path]) => path.toLowerCase())).size !== entries.length) {
      setError("Each file needs a unique path.");
      return;
    }
    const request: DraftRequest = {
      session_id: sessionId,
      request_id: crypto.randomUUID(),
      name,
      requested_scope: scope,
      scope_ref: scope === "personal" ? null : scopeRef,
      files: Object.fromEntries(entries),
    };
    if (!valid(request, sessionId)) {
      setError("Check the skill name, sharing target, file paths and size limits.");
      return;
    }
    setError("");
    setReview(request);
  };
  const save = async () => {
    if (running.current || !review || recovery.blocked) return;
    const request = pending ?? review;
    try {
      const encoded = JSON.stringify(request);
      sessionStorage.setItem(key, encoded);
      if (sessionStorage.getItem(key) !== encoded) throw new Error("Unable to preserve");
    } catch {
      setError("This tab could not preserve the request. No draft was sent.");
      return;
    }
    setPending(request);
    running.current = true;
    setSaving(true);
    setError("");
    try {
      const { data, response } = await api.POST("/heinzel/propose-skill-draft", { body: request });
      if (!response.ok || !data || !exact(data, request)) throw new Error("Unconfirmed");
      sessionStorage.setItem(lastKey, request.request_id);
      if (sessionStorage.getItem(lastKey) !== request.request_id) throw new Error("Unable to preserve receipt");
      sessionStorage.removeItem(key);
      if (sessionStorage.getItem(key) !== null) throw new Error("Unable to clear pending request");
      setPending(null);
      setSaved(data);
    } catch {
      setError("Could not confirm the save. Retry the same request; it will not publish or share the skill.");
    } finally {
      running.current = false;
      setSaving(false);
    }
  };
  return (
    <details
      className="rounded-lg border border-border p-3"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer font-medium">
        Skill draft{pending ? (saving ? " · saving" : " · retry needed") : ""}
      </summary>
      <p className="mt-2 text-sm text-muted-foreground">
        Drafts are private. The requested sharing scope is not verified and grants no access. Saving does not publish or
        execute files.
      </p>
      {error && (
        <p role="alert" className="text-sm">
          {error}
        </p>
      )}
      {saved ? (
        <section className="mt-3 space-y-2">
          <h3 className="break-all">Saved private draft: {saved.name}</h3>
          <p className="break-all">
            Requested scope: {saved.requested_scope}
            {saved.scope_ref ? ` · ${saved.scope_ref}` : ""} (not shared)
          </p>
          <p className="break-all text-xs">Draft ID: {saved.request_id}</p>
          <p className="break-all text-xs">Draft fingerprint: {saved.preview_sha256}</p>
          {Object.entries(saved.files).map(([path, content]) => (
            <div key={path}>
              <h4 className="break-all font-medium">{path}</h4>
              <pre className="whitespace-pre-wrap break-all rounded border p-2 text-xs">{content}</pre>
            </div>
          ))}
          {saved.requested_scope !== "personal" && <SkillSharingPanel key={saved.request_id} draft={saved} />}
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setSaved(null);
              setReview(null);
              setName("");
              setScope("personal");
              setScopeRef("");
              setFiles([{ id: "main", path: "SKILL.md", text: "" }]);
            }}
          >
            Create another draft
          </Button>
        </section>
      ) : review ? (
        <section className="mt-3 space-y-2" aria-label="Review skill draft">
          <h3 className="font-medium">Review exact draft</h3>
          <p className="break-all">Name: {review.name}</p>
          <p className="break-all">
            Requested scope: {review.requested_scope}
            {review.scope_ref ? ` · ${review.scope_ref}` : ""} (not shared)
          </p>
          {Object.entries(review.files).map(([path, content]) => (
            <div key={path}>
              <h4 className="break-all font-medium">{path}</h4>
              <pre className="whitespace-pre-wrap break-all rounded border p-2 text-xs">{content}</pre>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={saving || recovery.blocked} onClick={() => void save()}>
              {pending ? "Retry same draft" : "Save private draft"}
            </Button>
            {!pending && (
              <Button type="button" variant="outline" onClick={() => setReview(null)}>
                Edit draft
              </Button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Until the save is confirmed, this tab retains the draft for retry.
          </p>
        </section>
      ) : (
        <form
          className="mt-3 space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            prepare();
          }}
        >
          <fieldset disabled={saving} className="space-y-2">
            <legend className="sr-only">Skill draft fields</legend>
            <div className="space-y-1 border-b border-border pb-2">
              <label className="block text-sm">
                Open a saved draft by ID
                <input
                  className="block w-full rounded border bg-background p-2"
                  value={lookupId}
                  maxLength={128}
                  onChange={(event) => setLookupId(event.target.value)}
                />
              </label>
              <Button
                type="button"
                variant="outline"
                disabled={saving || recovery.blocked || !matches(requestId, lookupId) || hasEdits}
                onClick={() => void openDraft()}
              >
                Open saved draft
              </Button>
            </div>
            <div className="space-y-1 border-b border-border pb-2">
              <Button
                type="button"
                variant="outline"
                disabled={saving || recovery.blocked || hasEdits}
                onClick={() => void listDrafts(null)}
              >
                Load saved drafts
              </Button>
              {draftRefs.map((id) => (
                <Button
                  key={id}
                  type="button"
                  variant="outline"
                  className="w-full justify-start whitespace-normal break-all text-left"
                  disabled={saving || recovery.blocked || hasEdits}
                  onClick={() => void openDraft(id)}
                >
                  Open draft {id}
                </Button>
              ))}
              {draftCursor !== null && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={saving || recovery.blocked || hasEdits}
                  onClick={() => void listDrafts(draftCursor)}
                >
                  More drafts
                </Button>
              )}
            </div>
            <label className="block text-sm">
              Skill name
              <input
                className="block w-full rounded border bg-background p-2"
                value={name}
                maxLength={64}
                required
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className="block text-sm">
              Requested sharing scope
              <select
                className="block w-full rounded border bg-background p-2"
                value={scope}
                onChange={(event) => setScope(event.target.value as Scope)}
              >
                {scopes.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            {scope !== "personal" && (
              <label className="block text-sm">
                Scope reference
                <input
                  className="block w-full rounded border bg-background p-2"
                  value={scopeRef}
                  maxLength={128}
                  required
                  onChange={(event) => setScopeRef(event.target.value)}
                />
              </label>
            )}
            {files.map((file, index) => (
              <fieldset key={file.id} className="space-y-1 rounded border p-2">
                <legend className="text-sm">File {index + 1}</legend>
                <label className="block text-sm">
                  Path
                  <input
                    className="block w-full rounded border bg-background p-2"
                    value={file.path}
                    readOnly={index === 0}
                    required
                    onChange={(event) =>
                      setFiles((current) =>
                        current.map((item) => (item.id === file.id ? { ...item, path: event.target.value } : item)),
                      )
                    }
                  />
                </label>
                <label className="block text-sm">
                  Contents
                  <textarea
                    className="block w-full rounded border bg-background p-2 font-mono"
                    value={file.text}
                    required={index === 0}
                    onChange={(event) =>
                      setFiles((current) =>
                        current.map((item) => (item.id === file.id ? { ...item, text: event.target.value } : item)),
                      )
                    }
                  />
                </label>
                {index > 0 && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setFiles((current) => current.filter((item) => item.id !== file.id))}
                  >
                    Remove file
                  </Button>
                )}
              </fieldset>
            ))}
            <p className="text-xs text-muted-foreground">
              Use SKILL.md plus text under references/, templates/ or assets/, and Python files under scripts/. Up to 16
              files; each at most 24 KiB and the encoded form under 47 KiB.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={files.length >= 16}
                onClick={() => setFiles((current) => [...current, { id: crypto.randomUUID(), path: "", text: "" }])}
              >
                Add supporting file
              </Button>
              <Button type="submit" disabled={recovery.blocked}>
                Review draft
              </Button>
            </div>
          </fieldset>
        </form>
      )}
    </details>
  );
}
