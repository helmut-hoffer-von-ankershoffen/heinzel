import { Pause, Play, X } from "lucide-react";
import { useRef, useState } from "react";
import { PaneAction } from "@/components/PaneActions";

export type AlbumTransfer = {
  phase: "idle" | "downloading" | "paused" | "cancelled" | "ready" | "failed";
  verified: number;
  total: number;
  verifiedBytes: number;
  receivedBytes?: number;
  totalBytes: number;
};

export type AlbumPosition = { elapsedSeconds: number; durationSeconds: number };
type TransferAction = "pauseDownload" | "resumeDownload" | "cancelDownload";

function time(seconds: number): string {
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  const remainder = String(whole % 60).padStart(2, "0");
  return `${minutes}:${remainder}`;
}

export function AlbumProgress({
  transfer,
  position,
  onSeek,
  onTransfer,
}: {
  transfer: AlbumTransfer;
  position?: AlbumPosition;
  onSeek: (seconds: number) => void;
  onTransfer: (action: TransferAction) => void;
}) {
  const dragging = useRef(false);
  const [draft, setDraft] = useState<number>();
  const downloading = transfer.phase === "downloading";
  const paused = transfer.phase === "paused";
  const resumable = paused || transfer.phase === "cancelled" || transfer.phase === "failed";
  if (transfer.total > transfer.verified && (downloading || resumable)) {
    const received = Math.min(transfer.totalBytes, Math.max(0, transfer.receivedBytes ?? transfer.verifiedBytes));
    const label = downloading ? "Saving album" : `Album download ${transfer.phase}`;
    return (
      <fieldset className="flex min-w-0 items-center gap-1" aria-label="Album download status">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground tabular-nums" role="status">
            {label} · {transfer.verified}/{transfer.total}
          </p>
          <progress
            aria-label="Album download"
            aria-valuetext={`${received} of ${transfer.totalBytes} bytes`}
            value={transfer.totalBytes > 0 ? received : undefined}
            max={Math.max(1, transfer.totalBytes)}
            className="block h-1 w-full accent-primary"
          />
        </div>
        {downloading && (
          <PaneAction ghost icon={Pause} onClick={() => onTransfer("pauseDownload")}>
            Pause download
          </PaneAction>
        )}
        {resumable && (
          <PaneAction ghost icon={Play} onClick={() => onTransfer("resumeDownload")}>
            Resume download
          </PaneAction>
        )}
        {(downloading || paused) && (
          <PaneAction ghost icon={X} onClick={() => onTransfer("cancelDownload")}>
            Cancel download (keep verified files)
          </PaneAction>
        )}
      </fieldset>
    );
  }

  if (!position || !Number.isFinite(position.durationSeconds) || position.durationSeconds <= 0) return null;
  const { durationSeconds } = position;
  const elapsed = Number.isFinite(position.elapsedSeconds)
    ? Math.min(durationSeconds, Math.max(0, position.elapsedSeconds))
    : 0;
  const shown = draft ?? elapsed;
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground tabular-nums">
      <span aria-hidden>{time(shown)}</span>
      <input
        type="range"
        data-album-seek
        aria-label="Seek in track"
        aria-valuetext={`${time(shown)} of ${time(durationSeconds)}`}
        min={0}
        max={durationSeconds}
        step="any"
        value={shown}
        className="min-w-0 flex-1 accent-primary"
        onPointerDown={() => {
          dragging.current = true;
        }}
        onPointerCancel={() => {
          dragging.current = false;
          setDraft(undefined);
        }}
        onPointerUp={() => {
          dragging.current = false;
          if (draft !== undefined) onSeek(draft);
          setDraft(undefined);
        }}
        onChange={(event) => {
          const target = event.currentTarget.valueAsNumber;
          if (dragging.current) setDraft(target);
          else onSeek(target);
        }}
      />
      <span aria-hidden>{time(durationSeconds)}</span>
    </div>
  );
}
