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
