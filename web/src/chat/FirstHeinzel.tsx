// The first start when every chat is with a Heinzel (ADR 0157, canvas boards 1, 17, 17b and 18): with no Heinzel
// yet the chat shows "Meet your first Heinzelmännchen": name, look, mission. The same form is step 7 of the onboarding.
import { PanelRightClose } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { MeetHeinzel, settleFirstHeinzel } from "./MeetHeinzel";

export { settleFirstHeinzel };

export function FirstHeinzel({ onCreated, onClose }: { onCreated: (sessionId: string) => void; onClose: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="chat-panel-header flex h-12 shrink-0 items-center border-b border-border px-2">
        <span className="flex-1" />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          data-pane-action
          onClick={onClose}
          aria-label={t("chat.collapse")}
          title={t("chat.collapse")}
        >
          <PanelRightClose aria-hidden />
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col items-center gap-4 overflow-y-auto p-6">
        <h2 className="text-center text-xl font-semibold">{t("oneChat.meet")}</h2>
        <MeetHeinzel onCreated={onCreated} />
      </div>
    </div>
  );
}
