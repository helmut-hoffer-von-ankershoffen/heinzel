// Local references only: the registry does not contact Google or verify access or ownership.
import { useEffect, useId, useState } from "react";
import { api, type SchemaPersonalArtifact, type SchemaSessionSummary } from "@/api/client";
import { t } from "@/i18n";

function safeGoogleLink(item: SchemaPersonalArtifact, sessionId: string): boolean {
  const paths = { document: "document", spreadsheet: "spreadsheets", presentation: "presentation" } as const;
  if (item.session_id !== sessionId || item.provider !== "google" || item.provenance !== "principal-submitted")
    return false;
  if (!Object.hasOwn(paths, item.kind) || typeof item.ref !== "string") return false;
  const path = paths[item.kind];
  return (
    new RegExp(`^https://docs\\.google\\.com/${path}/d/[A-Za-z0-9_-]{10,128}/edit$`).exec(item.ref)?.[0] === item.ref
  );
}

const names = { document: "Google Doc", spreadsheet: "Google Sheet", presentation: "Google Slides" } as const;

/** The current owner's local links, not provider-verified files or evidence of agent edits. */
export function PersonalArtifactLinks({ sessionId, compact = false }: { sessionId: string; compact?: boolean }) {
  const headingId = useId();
  const [result, setResult] = useState<{ sessionId: string; artifacts: SchemaPersonalArtifact[] } | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    setResult(null);
    setError(false);
    void api
      .GET("/heinzel/personal-artifacts", { params: { query: { session_id: sessionId } } })
      .then(({ data, response }) => {
        if (
          !response.ok ||
          !data ||
          data.session_id !== sessionId ||
          !Array.isArray(data.artifacts) ||
          data.artifacts.some((item) => !safeGoogleLink(item, sessionId))
        )
          throw new Error("Invalid associations");
        if (active) setResult({ sessionId, artifacts: data.artifacts });
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, [sessionId]);

  const artifacts = result?.sessionId === sessionId ? result.artifacts : null;
  return (
    <section
      aria-labelledby={headingId}
      className={compact ? "space-y-1 rounded border border-border p-2 text-sm" : "space-y-2"}
    >
      <h3 id={headingId} className="font-medium">
        {compact ? "Associated files" : "Local associations"}
      </h3>
      {compact && (
        <p className="text-xs text-muted-foreground">{t("artifacts.note")}</p>
      )}
      {error ? (
        <p role="alert">Associations could not be loaded or validated.</p>
      ) : artifacts === null ? (
        <p role="status">Loading associations…</p>
      ) : artifacts.length === 0 ? (
        <p>{t("artifacts.none")}</p>
      ) : (
        <ul className="space-y-1">
          {artifacts.map((item) => (
            <li key={item.request_id} className="rounded border border-border p-2">
              <a href={item.ref} target="_blank" rel="noopener noreferrer" className="break-all text-primary underline">
                {names[item.kind]} · {item.ref.split("/")[5]}
              </a>
              <p className="text-xs text-muted-foreground">{t("artifacts.note")}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function PersonalArtifacts() {
  const [sessions, setSessions] = useState<SchemaSessionSummary[] | null>(null);
  const [sessionError, setSessionError] = useState(false);
  const [selected, setSelected] = useState("");
  useEffect(() => {
    let active = true;
    void api
      .GET("/heinzel/sessions")
      .then(({ data, response }) => {
        if (!response.ok || !data) throw new Error("Unavailable");
        if (active) setSessions(data.sessions ?? []);
      })
      .catch(() => {
        if (active) setSessionError(true);
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <section aria-labelledby="personal-artifacts" className="space-y-3 rounded-lg border border-border p-4">
      <h2 id="personal-artifacts" className="font-semibold">
        {t("artifacts.title")}
      </h2>
      <p className="text-sm text-muted-foreground">{t("artifacts.note")}</p>
      {sessionError ? (
        <p role="alert">{t("artifacts.loadError")}</p>
      ) : sessions === null ? (
        <p role="status">Loading personal helpers…</p>
      ) : (
        <label className="block space-y-1 text-sm">
          <span>{t("artifacts.choose")}</span>
          <select
            className="block w-full rounded border border-border bg-background p-2"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="">{t("artifacts.pick")}</option>
            {sessions.map((session) => (
              <option key={session.session_id} value={session.session_id}>
                {session.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {selected && <PersonalArtifactLinks key={selected} sessionId={selected} />}
    </section>
  );
}
