// The look of the personal Heinzel behind a notice (ADR 0043, 0049). A notice records who speaks (name, hat, reason)
// and links to that Heinzel's conversation; its full, current look comes from the list of personal Heinzels, so a
// notice shows the same figure as the chat, also after the look was changed. Without that Heinzel in the list (it is
// another person's, or gone) the recorded hat is what remains.
import { useQuery } from "@tanstack/react-query";
import { api, type SchemaNotice, unwrap } from "@/api/client";
import type { HeinzelLook } from "@/components/heinzel";
import { appearanceLook } from "@/heinzel/look";

/** The personal session a notice links to, from its route `/heinzel?chat=<session>`. */
export function noticeSession(notice: Pick<SchemaNotice, "route">): string | null {
  const query = notice.route.split("?")[1];
  return query ? new URLSearchParams(query.split("#")[0]).get("chat") : null;
}

/** For each notice with a Heinzel behind it: that Heinzel's look; null for every other notice. */
export function useNoticeLook(notices: readonly SchemaNotice[]): (notice: SchemaNotice) => HeinzelLook | null {
  const sessions = useQuery({
    queryKey: ["heinzel", "sessions"],
    queryFn: () => unwrap(api.GET("/heinzel/sessions")),
    enabled: notices.some((notice) => notice.helper),
    staleTime: 30_000,
    retry: false,
  });
  return (notice) => {
    if (!notice.helper) return null;
    const id = noticeSession(notice);
    const session = id ? sessions.data?.sessions?.find((item) => item.session_id === id) : undefined;
    return session ? appearanceLook(session.appearance) : { hat: notice.helper.hat ?? undefined };
  };
}
