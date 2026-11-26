import { RefreshCw } from "lucide-react";
import { refreshIcon } from "@/lib/refreshFeedback";

/** A read's real loading and failure state, shared by text refresh buttons. */
export function RefreshGlyph({ busy, failed }: { busy: boolean; failed: boolean }) {
  return <RefreshCw aria-hidden className={`size-4 ${refreshIcon(busy, failed)}`} />;
}
