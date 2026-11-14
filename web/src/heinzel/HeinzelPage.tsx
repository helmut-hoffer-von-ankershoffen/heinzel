// One page per Heinzel (`/heinzel/:id`, canvas boards Profile, Lifecycle and Doing): its look and name, mission,
// schedule, intelligence, how it is doing, pause and retire, then the reviews and records the chat's old Details held.
// More → About in the chat opens it. The chat stays beside it (on a phone, in its tab): there is no "Back to chat".
// A new address moves focus to the page's heading, so a screen reader hears where it landed.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useParams } from "react-router";
import { api, problem } from "@/api/client";
import { useHeinzelSessions } from "@/api/queries";
import { useConversationBusy } from "@/chat/conversationBusy";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { sessionWord } from "@/heinzel/activity";
import { IntelligenceSection } from "@/heinzel/IntelligenceSection";
import { appearanceLook, stateLabel } from "@/heinzel/look";
import { t } from "@/i18n";
import { isDurableSession } from "@/lib/chatIntent";
import { DoingSection } from "./page/DoingSection";
import { LifecycleSection } from "./page/LifecycleSection";
import { LookSection } from "./page/LookSection";
import { MissionSection } from "./page/MissionSection";
import { ReviewsSection } from "./page/ReviewsSection";
import { ScheduleSection } from "./page/ScheduleSection";

export function HeinzelPage() {
  const { id = "" } = useParams();
  const valid = isDurableSession(id);
  const heading = useRef<HTMLHeadingElement>(null);
  const snapshot = useQuery({
    // The chat's own key for its first page: a save here shows in the chat header at once, and the other way round.
    queryKey: ["heinzel", "session", id],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/heinzel/session", { params: { query: { session_id: id } } });
      if (!response.ok || !data) throw problem(response.status, error);
      if (data.session_id !== id) throw new Error("Unexpected personal session");
      return data;
    },
    enabled: valid,
    retry: false,
  });
  const sessions = useHeinzelSessions(valid);
  const chatBusy = useConversationBusy(id);
  const summary = sessions.data?.sessions?.find((item) => item.session_id === id);
  const data = snapshot.data?.session_id === id ? snapshot.data : undefined;
  const loaded = data !== undefined;
  // biome-ignore lint/correctness/useExhaustiveDependencies: a new Heinzel's address moves focus too, even when cached
  useEffect(() => {
    if (loaded) heading.current?.focus();
  }, [loaded, id]);
  if (!valid || snapshot.isError)
    return (
      <div className="mx-auto max-w-3xl p-6">
        <p role="alert">{t("page.loadError")}</p>
      </div>
    );
  if (!data)
    return (
      <div className="mx-auto max-w-3xl p-6">
        <p role="status">{t("state.loading")}</p>
      </div>
    );
  // The one word of 5b: a retired Heinzel is Retired, whatever its last counts said.
  const word = summary ? sessionWord({ state: data.state, activity: summary.activity }) : stateLabel(data.state);
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 p-4 sm:p-6">
      <header className="flex flex-wrap items-center gap-3">
        <HeinzelAvatar view="bust" {...appearanceLook(data.appearance)} className="size-14 shrink-0 overflow-hidden" />
        <div className="min-w-0 flex-1">
          <h1 ref={heading} tabIndex={-1} className="truncate text-xl font-semibold outline-none">
            {t("page.title", { name: data.name })}
          </h1>
          <p className="text-sm text-muted-foreground">{word}</p>
        </div>
      </header>
      <LookSection key={`look:${id}`} snapshot={data} />
      <MissionSection snapshot={data} />
      <ScheduleSection schedule={data.schedule} />
      <IntelligenceSection sessionId={id} />
      <DoingSection summary={summary} />
      <LifecycleSection key={`lifecycle:${id}`} snapshot={data} />
      <ReviewsSection key={`reviews:${id}`} snapshot={data} busy={chatBusy} />
    </div>
  );
}
