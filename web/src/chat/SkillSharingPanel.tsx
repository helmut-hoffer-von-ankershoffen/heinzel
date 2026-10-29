import { useRef, useState } from "react";
import {
  api,
  type SchemaHeinzelProposeSkillSharingInput,
  type SchemaSkillDraft,
  type SchemaSkillSharingProposal,
} from "@/api/client";
import { Button } from "@/components/ui/button";

type Request = SchemaHeinzelProposeSkillSharingInput;
const id = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

export function SkillSharingPanel({ draft }: { draft: SchemaSkillDraft }) {
  const key = `hz.skill-sharing.pending.${draft.session_id}.${draft.request_id}`;
  const [recovery] = useState(() => {
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw) return { request: null, blocked: false };
      const value = JSON.parse(raw) as Request;
      if (
        value.session_id !== draft.session_id ||
        value.draft_request_id !== draft.request_id ||
        value.draft_sha256 !== draft.preview_sha256 ||
        Object.keys(value).sort().join(",") !== "draft_request_id,draft_sha256,request_id,session_id" ||
        !id.test(value.request_id)
      )
        return { request: null, blocked: true };
      return { request: value, blocked: false };
    } catch {
      return { request: null, blocked: true };
    }
  });
  const [pending, setPending] = useState<Request | null>(recovery.request);
  const [proposal, setProposal] = useState<SchemaSkillSharingProposal | null>(null);
  const [refs, setRefs] = useState<string[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const [error, setError] = useState("");

  function matches(value: SchemaSkillSharingProposal, requestId: string): boolean {
    return (
      value.session_id === draft.session_id &&
      value.request_id === requestId &&
      value.draft_request_id === draft.request_id &&
      value.draft_sha256 === draft.preview_sha256 &&
      value.requested_scope === draft.requested_scope &&
      value.scope_ref === draft.scope_ref &&
      value.status === "awaiting_review" &&
      value.authority === "unverified"
    );
  }

  async function submit() {
    if (running.current || recovery.blocked) return;
    running.current = true;
    const request: Request = pending ?? {
      session_id: draft.session_id,
      request_id: crypto.randomUUID(),
      draft_request_id: draft.request_id,
      draft_sha256: draft.preview_sha256,
    };
    try {
      const encoded = JSON.stringify(request);
      sessionStorage.setItem(key, encoded);
      if (sessionStorage.getItem(key) !== encoded) throw new Error("storage");
    } catch {
      setError("Could not preserve the request for retry. Nothing was sent.");
      running.current = false;
      return;
    }
    setPending(request);
    setBusy(true);
    setError("");
    try {
      const { data, response } = await api.POST("/heinzel/propose-skill-sharing", { body: request });
      if (!response.ok || !data || !matches(data, request.request_id)) throw new Error("unconfirmed");
      sessionStorage.removeItem(key);
      if (sessionStorage.getItem(key) !== null) throw new Error("storage");
      setPending(null);
      setProposal(data);
    } catch {
      setError("Could not confirm the request. Retry the same request; no sharing has been approved.");
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  async function list(next: number | null) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    try {
      const { data, response } = await api.GET("/heinzel/skill-sharing-proposals", {
        params: { query: { session_id: draft.session_id, cursor: next ?? 0, limit: 20 } },
      });
      if (!response.ok || !data) throw new Error("unavailable");
      const candidates = await Promise.all(
        data.items.map(async (item) => {
          if (!id.test(item.request_id)) throw new Error("invalid request");
          const result = await api.GET("/heinzel/skill-sharing", {
            params: { query: { session_id: draft.session_id, request_id: item.request_id } },
          });
          const value = result.data;
          if (
            !result.response.ok ||
            !value ||
            value.session_id !== draft.session_id ||
            value.request_id !== item.request_id
          )
            throw new Error("invalid request");
          return matches(value, item.request_id) ? item.request_id : null;
        }),
      );
      const matching = candidates.filter((candidate): candidate is string => candidate !== null);
      setRefs((old) => (next === null ? matching : [...old, ...matching]));
      setCursor(data.next_cursor ?? null);
    } catch {
      setError("Sharing requests could not be loaded.");
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  async function open(requestId: string) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    setProposal(null);
    try {
      const { data, response } = await api.GET("/heinzel/skill-sharing", {
        params: { query: { session_id: draft.session_id, request_id: requestId } },
      });
      if (!response.ok || !data || !matches(data, requestId)) throw new Error("different draft");
      setProposal(data);
    } catch {
      setError("This request is unavailable or belongs to another draft.");
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  return (
    <section aria-label="Skill sharing request" className="space-y-2 rounded border p-3 text-sm">
      <h4 className="font-medium">Request sharing review</h4>
      <p>
        Target: {draft.requested_scope} · {draft.scope_ref}
      </p>
      <p>
        Review the exact files and fingerprint above. Membership and authority are unverified. This request does not
        approve or publish the skill, or grant access.
      </p>
      {recovery.blocked && (
        <p role="alert">This tab's pending request cannot be recovered. No new request can be sent.</p>
      )}
      {error && <p role="alert">{error}</p>}
      {proposal && (
        <p role="status">Request {proposal.request_id}: awaiting review; authority unverified. Not shared.</p>
      )}
      <Button type="button" disabled={busy || recovery.blocked} onClick={() => void submit()}>
        {pending ? "Retry same sharing request" : "Request review of sharing"}
      </Button>
      <div className="flex flex-col gap-2">
        <Button type="button" variant="outline" disabled={busy} onClick={() => void list(null)}>
          Load sharing requests
        </Button>
        {refs.map((ref) => (
          <Button
            key={ref}
            type="button"
            variant="outline"
            className="w-full justify-start whitespace-normal break-all text-left"
            disabled={busy}
            onClick={() => void open(ref)}
          >
            Open request {ref}
          </Button>
        ))}
        {cursor !== null && (
          <Button type="button" variant="outline" disabled={busy} onClick={() => void list(cursor)}>
            More requests
          </Button>
        )}
      </div>
    </section>
  );
}
