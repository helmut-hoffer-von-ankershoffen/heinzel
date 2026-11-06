import { useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api, problem } from "@/api/client";
import { Button } from "@/components/ui/button";

// Pre-creation has no verified owner/profile recovery context. Never recover private content from origin storage.
const legacyKey = "hz.start-helper.pending";
function unboundRecoveryExists(): boolean {
  try {
    return localStorage.getItem(legacyKey) !== null;
  } catch {
    return true;
  }
}

/** One explicit model turn. Without verified recovery identity, uncertain requests cannot be replayed here. */
export function StartHelper() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [blocked] = useState(unboundRecoveryExists);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const running = useRef(false);
  const mounted = useRef(false);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const start = async () => {
    if (running.current || blocked || submitted || !name.trim() || !prompt.trim()) return;
    running.current = true;
    setBusy(true);
    setError(false);
    try {
      // Each mounted form owns its request; tabs never share, replace or clear a pending record.
      // Recheck legacy evidence immediately before submission, without reading its private contents.
      if (unboundRecoveryExists()) throw new Error("Unbound earlier request");
      const request = { name, prompt, request_id: crypto.randomUUID() };
      setSubmitted(request.request_id);
      setName("");
      setPrompt("");
      const { data, error: detail, response } = await api.POST("/heinzel/start-helper", { body: request });
      if (!response.ok || !data) throw problem(response.status, detail);
      if (data.request_id !== request.request_id || !/^hz-[0-9A-HJKMNP-TV-Z]{26}$/.test(data.session_id))
        throw new Error("Unexpected answer");
      await client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
      if (mounted.current) navigate(`/heinzel?chat=${encodeURIComponent(data.session_id)}`);
    } catch {
      if (mounted.current) setError(true);
    } finally {
      running.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  return (
    <details className="space-y-2 rounded border border-border p-3 text-sm">
      <summary className="cursor-pointer font-medium">Start a helper from your first message</summary>
      <p>
        This sends one message to your selected model. Suggestions do not grant source access, recurring turns or
        notifications. The connected helper feature must already be enabled.
      </p>
      <p>
        Private message recovery is unavailable before a helper exists. This form keeps no name or message in browser
        storage. Reload loses unsent text and this form’s outcome reference; an unresolved submission cannot be retried
        here.
      </p>
      {submitted && (
        <p role="status">
          Submitted request reference: {submitted}. This reference is not an approval or a replay instruction.
        </p>
      )}
      <form
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <label className="block">
          Helper name
          <input
            className="block w-full rounded border p-2"
            required
            maxLength={100}
            value={name}
            disabled={busy || !!submitted || blocked}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="block">
          First message
          <textarea
            className="block w-full rounded border p-2"
            required
            maxLength={32000}
            rows={4}
            value={prompt}
            disabled={busy || !!submitted || blocked}
            onChange={(event) => setPrompt(event.target.value)}
          />
        </label>
        <Button type="submit" disabled={busy || !!submitted || blocked || !name.trim() || !prompt.trim()}>
          {submitted ? "First message submitted" : "Send first message"}
        </Button>
      </form>
      {blocked && (
        <p role="alert">
          An earlier unbound request is present. Its contents are not recovered and it is not changed or sent. Inspect
          your existing helper and request evidence before continuing through a verified conversation.
        </p>
      )}
      {error && (
        <p role="alert">
          The result is unresolved or unavailable. Inspect your helper list and saved server outcomes. This form will
          not retry the request or create a replacement.
        </p>
      )}
    </details>
  );
}
