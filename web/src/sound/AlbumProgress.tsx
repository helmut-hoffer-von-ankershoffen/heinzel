import { Pause, Play, X } from "lucide-react";
import { type CSSProperties, useRef, useState } from "react";
import { PaneAction } from "@/components/PaneActions";
import "./album-progress.css";

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
  const showTransfer = transfer.total > transfer.verified && (downloading || resumable);
  const received = Math.min(transfer.totalBytes, Math.max(0, transfer.receivedBytes ?? transfer.verifiedBytes));
  const fraction = transfer.totalBytes > 0 ? received / transfer.totalBytes : 0;
  const percent = Math.min(99, Math.floor(fraction * 100));
  const transferLabel = downloading ? `Downloading album · ${percent}%` : `Album download ${transfer.phase}`;
  const duration = position?.durationSeconds ?? 0;
  const seekable = Number.isFinite(duration) && duration > 0;
  const elapsed =
    position && Number.isFinite(position.elapsedSeconds) && seekable
      ? Math.min(duration, Math.max(0, position.elapsedSeconds))
      : 0;
  const shown = draft ?? elapsed;
  const fill = {
    "--album-playback": `${seekable ? (shown / duration) * 100 : 0}%`,
  } as CSSProperties;

  return (
    <div className="min-w-0 text-xs text-muted-foreground tabular-nums">
      <div className="album-seek-rail" style={fill}>
        <div className="album-seek-track" aria-hidden>
          <span className="album-seek-played" />
          {showTransfer && <span className="album-seek-download" style={{ width: `${fraction * 100}%` }} />}
        </div>
        <input
          type="range"
          data-album-seek
          aria-label="Seek in track"
          aria-valuetext={seekable ? `${time(shown)} of ${time(duration)}` : "Duration unavailable"}
          min={0}
          max={seekable ? duration : 1}
          step="any"
          value={seekable ? shown : 0}
          disabled={!seekable}
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
      </div>
      <div className="flex min-w-0 items-start justify-between gap-2">
        <span aria-hidden>{seekable ? time(shown) : "—"}</span>
        {showTransfer && (
          <span
            className="text-center"
            role="progressbar"
            aria-label="Album download"
            aria-valuenow={percent}
            aria-valuetext={`${received} of ${transfer.totalBytes} bytes`}
          >
            {transferLabel}
          </span>
        )}
        <span aria-hidden>{seekable ? time(duration) : "—"}</span>
      </div>
      {showTransfer && (
        <fieldset className="flex min-w-0 items-center justify-center gap-1" aria-label="Album download actions">
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
      )}
    </div>
  );
}
