// Says where the next update goes on the chosen channel, with real versions, so a choice is clear before anything
// installs. Reading it changes nothing: the operation only switches when asked with `yes`.
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/api/client";

export function ChannelStatement({ value }: { value: unknown }) {
  const query = useQuery({
    queryKey: ["system", "channel", value],
    queryFn: () => unwrap(api.POST("/system/channel")),
  });
  if (!query.data) return null;
  return <p className="mt-1 text-sm">{query.data.statement}</p>;
}
