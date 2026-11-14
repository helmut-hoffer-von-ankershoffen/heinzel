// Reviews and records on the Heinzel's page: the panels the chat's old Details block held (board 12, rejected), here
// unchanged until the waiting panel (slice 11) takes the reviews over. A review link (`?notification-chat=` and the
// like) lands on this page; a review that is decided in the chat (`?agent-chat=`) opens the chat beside it.
import { useState, useSyncExternalStore } from "react";
import { useSearchParams } from "react-router";
import type { SchemaChatSession } from "@/api/client";
import { usePreviewFeature } from "@/api/queries";
import type { components } from "@/api/schema";
import { authorityEpoch, subscribeAuthority } from "@/api/token";
import { AgentProposal } from "@/chat/AgentProposal";
import { AgentProposalSummary } from "@/chat/AgentProposalSummary";
import { AgentSchedules } from "@/chat/AgentSchedules";
import { CapturedWriteActivity } from "@/chat/CapturedWriteActivity";
import { ConnectedReadReview } from "@/chat/ConnectedReadReview";
import { ConnectedReadSetReview } from "@/chat/ConnectedReadSetReview";
import { ContractHistory } from "@/chat/ContractHistory";
import { NotificationPanel } from "@/chat/NotificationPanel";
import { ReleaseDraftActivity } from "@/chat/ReleaseDraftActivity";
import { SkillDraftPanel } from "@/chat/SkillDraftPanel";
import { t } from "@/i18n";
import { OPEN_CHAT, requestComposerFocus } from "@/lib/chatIntent";
import { PersonalArtifactLinks } from "@/pages/PersonalArtifacts";

type Snapshot = components["schemas"]["SessionSnapshot"];

export function ReviewsSection({ snapshot, busy }: { snapshot: Snapshot; busy: boolean }) {
  // A retired Heinzel never wakes: its page keeps the records and offers no decision.
  if (snapshot.state === "retired") return <Records snapshot={snapshot} />;
  return <Reviews snapshot={snapshot} busy={busy} />;
}

function Records({ snapshot }: { snapshot: Snapshot }) {
  return (
    <section aria-label={t("page.records")} className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">{t("page.records")}</h2>
      <p className="text-sm text-muted-foreground">{t("page.retiredRecords")}</p>
      <PersonalArtifactLinks key={`artifacts:${snapshot.session_id}`} sessionId={snapshot.session_id} compact />
      <ContractHistory contracts={snapshot.contracts ?? []} />
    </section>
  );
}

function Reviews({ snapshot, busy }: { snapshot: Snapshot; busy: boolean }) {
  const sessionId = snapshot.session_id;
  const authority = useSyncExternalStore(subscribeAuthority, authorityEpoch);
  const capturedWrites = usePreviewFeature("captured_writes");
  const [, setParams] = useSearchParams();
  const [showProposal, setShowProposal] = useState(false);
  // The chat opens beside the page with this Heinzel (on a phone its tab), holding the review or the composer.
  const openChat = (review?: SchemaChatSession) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("chat", sessionId);
      if (review) next.set("agent-chat", review.id);
      return next;
    });
    window.dispatchEvent(new Event(OPEN_CHAT));
  };
  // Asking about sources is said in the chat: it opens, and the cursor waits in its composer.
  const ask = () => {
    openChat();
    requestComposerFocus();
  };
  const terms = (
    <details
      className="rounded-lg border border-border p-3"
      onToggle={(event) => {
        if (event.currentTarget.open) setShowProposal(true);
      }}
    >
      <summary className="cursor-pointer text-sm font-medium">{t("page.reviews.terms")}</summary>
      {showProposal && <AgentProposal sessionId={sessionId} />}
    </details>
  );
  const termsSummary = <AgentProposalSummary sessionId={sessionId} />;
  const sourceReview = (
    <>
      <ConnectedReadReview key={`connected:${sessionId}`} sessionId={sessionId} conversationBusy={busy} />
      <ConnectedReadSetReview key={`connected-set:${sessionId}`} sessionId={sessionId} onAsk={ask} />
    </>
  );
  const schedules = <AgentSchedules key={sessionId} sessionId={sessionId} />;
  return (
    <section aria-label={t("page.reviews")} className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">{t("page.reviews")}</h2>
      {terms}
      <PersonalArtifactLinks key={`artifacts:${sessionId}`} sessionId={sessionId} compact />
      {termsSummary}
      <NotificationPanel key={`notification:${sessionId}`} sessionId={sessionId} />
      {sourceReview}
      <ContractHistory contracts={snapshot.contracts ?? []} />
      <SkillDraftPanel key={`skills:${sessionId}`} sessionId={sessionId} />
      {schedules}
      {capturedWrites && (
        <CapturedWriteActivity key={sessionId} sessionId={sessionId} conversationBusy={busy} onReview={openChat} />
      )}
      <ReleaseDraftActivity
        key={`release-draft-${sessionId}`}
        authority={authority}
        sessionId={sessionId}
        conversationBusy={busy}
        onReview={openChat}
      />
    </section>
  );
}
