// These endpoints are supplied by the revisioned, inert proposal backend. No execution or approval is implied.
import { useEffect, useRef, useState } from "react";
import { api, problem, type SchemaScheduledAgentProposal } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

type Proposal = SchemaScheduledAgentProposal;
type Request = {
  session_id: string;
  request_id: string;
  contract: string;
  max_usd: number;
  timeout_seconds: number;
  expected_revision?: number;
};

async function latestProposal(sessionId: string): Promise<Proposal | null> {
  const { data, error, response } = await api.GET("/heinzel/latest-scheduled-agent-proposal", {
    params: { query: { session_id: sessionId } },
  });
  if (!response.ok || data === undefined) throw problem(response.status, error);
  return data;
}

async function writeProposal(request: Request): Promise<Proposal> {
  const { expected_revision, ...terms } = request;
  const outcome =
    expected_revision === undefined
      ? await api.POST("/heinzel/propose-scheduled-agent", { body: terms })
      : await api.POST("/heinzel/amend-scheduled-agent", { body: { ...terms, expected_revision } });
  if (!outcome.response.ok || !outcome.data) throw problem(outcome.response.status, outcome.error);
  return outcome.data;
}

export function AgentProposal({ sessionId }: { sessionId: string }) {
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [contract, setContract] = useState("");
  const [budget, setBudget] = useState("0.10");
  const [timeout, setTimeoutSeconds] = useState("30");
  const storageKey = `hz.agent-proposal.pending.${sessionId}`;
  const [recovery] = useState(() => {
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (!raw) return { request: null, blocked: false };
      const saved = JSON.parse(raw) as Request;
      if (
        saved.session_id !== sessionId ||
        !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(saved.request_id) ||
        typeof saved.contract !== "string" ||
        !saved.contract.length ||
        saved.contract.length > 4000 ||
        !Number.isFinite(saved.max_usd) ||
        saved.max_usd <= 0 ||
        !Number.isInteger(saved.timeout_seconds) ||
        saved.timeout_seconds < 1 ||
        saved.timeout_seconds > 300 ||
        (saved.expected_revision !== undefined &&
          (!Number.isInteger(saved.expected_revision) || saved.expected_revision < 1))
      ) {
        return { request: null, blocked: true };
      }
      return { request: saved, blocked: false };
    } catch {
      return { request: null, blocked: true };
    }
  });
  const [pending, setPending] = useState<Request | null>(recovery.request);
  const busy = useRef(false);
  const [saving, setSaving] = useState(false);
  const load = async () => {
    const current = await latestProposal(sessionId);
    if (
      current &&
      (current.session_id !== sessionId || current.status !== "proposed" || current.permitted_tools.length !== 0)
    )
      throw new Error("Invalid proposal");
    setProposal(current);
    if (current) {
      setContract(current.contract);
      setBudget(current.max_usd === null ? "" : String(current.max_usd));
      setTimeoutSeconds(String(current.timeout_seconds));
    }
    return current;
  };
  useEffect(() => {
    let active = true;
    void latestProposal(sessionId)
      .then((current) => {
        if (!active) return;
        if (
          current &&
          (current.session_id !== sessionId || current.status !== "proposed" || current.permitted_tools.length !== 0)
        )
          throw new Error("Invalid proposal");
        setProposal(current);
        const draft = recovery.request ?? current;
        if (draft) {
          setContract(draft.contract);
          setBudget(draft.max_usd === null ? "" : String(draft.max_usd));
          setTimeoutSeconds(String(draft.timeout_seconds));
        }
      })
      .catch(() => {
        if (active) setError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [sessionId, recovery.request]);
  const save = async () => {
    if (busy.current || recovery.blocked) return;
    const request = pending ?? {
      session_id: sessionId,
      request_id: crypto.randomUUID(),
      contract,
      max_usd: Number(budget),
      timeout_seconds: Number(timeout),
      ...(proposal ? { expected_revision: proposal.revision } : {}),
    };
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(request));
    } catch {
      setError(true);
      return;
    }
    setPending(request);
    setSaving(true);
    busy.current = true;
    setError(false);
    try {
      const saved = await writeProposal(request);
      if (
        !saved ||
        saved.session_id !== sessionId ||
        saved.request_id !== request.request_id ||
        saved.contract !== request.contract ||
        saved.max_usd !== request.max_usd ||
        saved.timeout_seconds !== request.timeout_seconds ||
        saved.status !== "proposed" ||
        saved.permitted_tools.length !== 0
      )
        throw new Error("Invalid proposal acknowledgement");
      const latest = await load();
      if (!latest) throw new Error("Missing proposal");
      sessionStorage.removeItem(storageKey);
      setPending(null);
    } catch {
      setError(true);
    } finally {
      busy.current = false;
      setSaving(false);
    }
  };
  return (
    <section aria-label="Scheduled agent proposal" className="space-y-2 rounded-lg border border-border p-3 text-sm">
      <h3 className="font-medium">Scheduled agent proposal</h3>
      <p>{t("proposal.intro")}</p>
      {loading ? (
        <p role="status">Loading proposal…</p>
      ) : (
        <>
          {proposal && (
            <div className="space-y-1">
              <p>Revision {proposal.revision} · Proposed</p>
              <p className="whitespace-pre-wrap">{proposal.contract}</p>
              <p>
                {proposal.max_usd === null
                  ? "Subscription plan · cost unknown"
                  : `Proposed API limit: $${proposal.max_usd} per turn`}
                {` · ${proposal.timeout_seconds} seconds. This proposal grants no tools.`}
              </p>
              <p className="break-all">Preview: {proposal.preview_sha256}</p>
            </div>
          )}
          {proposal?.max_usd === null && !pending ? (
            <p>{t("proposal.askRevise")}</p>
          ) : (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void save();
              }}
              className="space-y-2"
            >
              <label className="block">
                Instructions
                <textarea
                  className="block w-full rounded border bg-background p-2"
                  required
                  maxLength={4000}
                  value={contract}
                  disabled={pending !== null}
                  onChange={(event) => setContract(event.target.value)}
                />
              </label>
              <label className="block">
                Maximum USD per turn
                <input
                  className="ml-2 rounded border bg-background p-1"
                  type="number"
                  min="0.01"
                  step="any"
                  required
                  value={budget}
                  disabled={pending !== null}
                  onChange={(event) => setBudget(event.target.value)}
                />
              </label>
              <label className="block">
                Timeout in seconds
                <input
                  className="ml-2 rounded border bg-background p-1"
                  type="number"
                  min="1"
                  max="300"
                  step="1"
                  required
                  value={timeout}
                  disabled={pending !== null}
                  onChange={(event) => setTimeoutSeconds(event.target.value)}
                />
              </label>
              <Button type="submit" disabled={saving || recovery.blocked || (error && !pending)}>
                {pending ? "Retry same proposal" : proposal ? "Propose revised terms" : "Propose terms"}
              </Button>
            </form>
          )}
        </>
      )}
      {recovery.blocked && <p role="alert">The pending proposal could not be recovered; no new proposal was sent.</p>}
      {error && <p role="alert">{t("proposal.confirmError")}</p>}
    </section>
  );
}
