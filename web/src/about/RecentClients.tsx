import { useQuery } from "@tanstack/react-query";
import { token } from "@/api/token";

type Observation = {
  kind: "unknown";
  platform: "unknown";
  presence: "fresh" | "stale";
};

/** Aggregate authenticated use is evidence of a request, never a live connection. */
export function RecentClients() {
  const query = useQuery({
    queryKey: ["web", "recent-clients"],
    queryFn: async ({ signal }): Promise<Observation[]> => {
      const response = await fetch("/api/recent-clients", {
        signal,
        cache: "no-store",
        headers: { Authorization: `Bearer ${token() ?? ""}` },
      });
      if (!response.ok) throw new Error("Client observations unavailable");
      return response.json();
    },
    retry: false,
    refetchInterval: 60_000,
  });
  return (
    <section className="mt-4 min-w-0" aria-label="Recent workspace use">
      <h3 className="font-medium">Recent workspace use</h3>
      <p className="text-muted-foreground">
        Aggregate authenticated API use of this workspace server since its last start. Fresh means within two hours;
        stale means within 30 days. This does not show live connections. Client type, OS flavor, version and app
        capabilities are unknown.
      </p>
      <DesktopObservations />
      {query.isError ? (
        <p>Client observations unavailable.</p>
      ) : !query.data ? (
        <p>Loading…</p>
      ) : query.data.length === 0 ? (
        <p>No recent use observed in this server lifetime. Earlier activity is unknown.</p>
      ) : (
        <ul>
          {query.data.map((client) => (
            <li key={client.presence} className="mt-2 break-words">
              {client.presence === "fresh" ? "Fresh observation" : "Stale observation"}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type DesktopObservation = {
  labels?: { flavor?: string; machine?: string; app_version?: string };
  identity_provenance?: string;
  device_id?: string | null;
  last_seen?: string | null;
  presence?: string;
};

function keyedObservations(rows: DesktopObservation[]) {
  const identities = rows.map((row) =>
    typeof row.device_id === "string" && row.device_id.trim()
      ? JSON.stringify(["device", row.device_id])
      : typeof row.labels?.flavor === "string" &&
          row.labels.flavor.trim() &&
          typeof row.labels?.machine === "string" &&
          row.labels.machine.trim()
        ? JSON.stringify(["session", row.labels.flavor, row.labels.machine])
        : null,
  );
  const counts = new Map<string, number>();
  for (const identity of identities) {
    if (identity !== null) counts.set(identity, (counts.get(identity) ?? 0) + 1);
  }
  const occurrences = new Map<string, number>();
  return rows.map((row, index) => {
    const identity = identities[index] ?? null;
    if (identity !== null && counts.get(identity) === 1) return { row, key: identity };
    // Ambiguous rows have no reliable backend identity. Equal content gets distinct occurrence slots.
    const content = JSON.stringify([
      identity,
      row.labels?.flavor,
      row.labels?.machine,
      row.labels?.app_version,
      row.identity_provenance,
      row.last_seen,
      row.presence,
    ]);
    const occurrence = occurrences.get(content) ?? 0;
    occurrences.set(content, occurrence + 1);
    return { row, key: JSON.stringify(["fallback", content, occurrence]) };
  });
}

/** Additive endpoint: its absence must not hide the older aggregate. */
function DesktopObservations() {
  const query = useQuery({
    queryKey: ["web", "client-observations"],
    queryFn: async ({ signal }): Promise<DesktopObservation[] | null> => {
      const response = await fetch("/api/client-observations", {
        signal,
        cache: "no-store",
        headers: { Authorization: `Bearer ${token() ?? ""}` },
      });
      if (response.status === 404 || response.status === 405) return null;
      if (!response.ok) throw new Error("Unavailable");
      const rows: unknown = await response.json();
      if (
        !Array.isArray(rows) ||
        rows.length > 50 ||
        rows.some((row) => !row || typeof row !== "object" || Array.isArray(row))
      )
        throw new Error("Invalid observations");
      return rows;
    },
    retry: false,
    refetchInterval: 60_000,
  });
  return (
    <section className="mt-3 min-w-0" aria-label="Recently observed desktop apps">
      <h3 className="font-medium">Recently observed desktop apps</h3>
      <p className="text-muted-foreground">
        App kind, device label and app version are self-reported. Device credentials identify a paired device; session
        observations only group matching labels, which can collide or change on rename. Last use is a successful API
        request, not an open or live connection. Platform, transport and capabilities are unknown. History clears when
        the server restarts; unlabelled and older clients may be absent.
      </p>
      {query.isError ? (
        <p>Desktop observations unavailable.</p>
      ) : query.data === null ? (
        <p>This server does not support desktop observations.</p>
      ) : !query.data ? (
        <p>Loading desktop observations…</p>
      ) : query.data.length === 0 ? (
        <p>No retained desktop observations.</p>
      ) : (
        <ul>
          {keyedObservations(query.data).map(({ row, key }) => (
            <li key={key} className="mt-2 min-w-0 break-words">
              <p>
                {label(row.labels?.flavor)} · {label(row.labels?.machine)} · App version{" "}
                {label(row.labels?.app_version)}
              </p>
              <p>
                {row.presence === "fresh" ? "Fresh" : row.presence === "stale" ? "Stale" : "Unknown age"} observation ·
                Last observed use: {observedTime(row.last_seen)}
              </p>
              <p>
                {row.identity_provenance === "device-credential"
                  ? "Identity: device credential"
                  : row.identity_provenance === "session-observation"
                    ? "Identity: session observation (label grouping only)"
                    : "Identity: unknown"}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function label(value: unknown): string {
  return typeof value === "string" && value.trim() ? value : "unknown";
}

function observedTime(value: unknown): string {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) return "unknown";
  return new Date(value).toLocaleString();
}
