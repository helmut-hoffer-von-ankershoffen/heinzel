import { useEffect, useRef, useState } from "react";
import type { SchemaSoundscapeView } from "@/api/client";
import { t } from "@/i18n";
import { PlayerCard } from "./PlayerCard";
import { page } from "./queries";
import { stop } from "./store";
import {
  ACCOUNT_SANDBOX,
  accountStream,
  command,
  embedSrc,
  FRAME_ATTRIBUTES,
  listen,
  playerEvent,
  readYouTubeAccount,
  rememberYouTubeAccount,
  type StreamView,
} from "./stream";

type Level = { gain: number; seconds: number; playing: boolean };
type Props = { soundscape: SchemaSoundscapeView; stream: StreamView; level: Level };
const STEP_MS = 100;
const PLAYBACK_WAIT_MS = 15_000;
const button = "min-h-8 rounded-lg border border-border px-2.5 py-1 hover:bg-accent";

/** A private player by default, with explicit account playback and recovery for restricted browsers (ADR 0092). */
export function StreamPlayer(props: Props) {
  const [account, setAccount] = useState(readYouTubeAccount);
  const [attempt, setAttempt] = useState(0);
  const alternative = accountStream(props.stream);
  const chooseAccount = (next: boolean) => {
    rememberYouTubeAccount(next);
    setAccount(next);
  };
  return (
    <Player
      key={`${account}:${attempt}`}
      {...props}
      stream={account && alternative ? alternative : props.stream}
      account={account && alternative !== null}
      chooseAccount={alternative ? chooseAccount : undefined}
      retry={() => setAttempt((was) => was + 1)}
    />
  );
}

function Player({
  soundscape,
  stream,
  level,
  account,
  chooseAccount,
  retry,
}: Props & { account: boolean; chooseAccount?: (next: boolean) => void; retry: () => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [help, setHelp] = useState(false);
  const [hasPlayed, setHasPlayed] = useState(false);
  const volume = useRef(0);
  const src = embedSrc(stream);
  const origin = stream.frame_origin;

  useEffect(() => {
    const onMessage = (message: MessageEvent) => {
      const data = playerEvent(message, frame.current, origin);
      if (!data) return;
      if (data.event === "onError" || data.event === "onAutoplayBlocked") {
        setHelp(true);
      }
      const state =
        data.event === "onStateChange"
          ? data.info
          : data.info && typeof data.info === "object" && "playerState" in data.info
            ? data.info.playerState
            : undefined;
      if (state === 1) setHasPlayed(true);
      if (data.event !== "onReady" && data.event !== "initialDelivery") return;
      // The frame starts silent so a retry cannot jump to YouTube's own volume.
      command(frame.current, origin, "setVolume", [volume.current]);
      setReady(true);
    };
    const onViolation = (event: SecurityPolicyViolationEvent) => {
      if (event.effectiveDirective === "frame-src" && event.blockedURI.startsWith(origin)) setBlocked(true);
    };
    window.addEventListener("message", onMessage);
    document.addEventListener("securitypolicyviolation", onViolation);
    return () => {
      window.removeEventListener("message", onMessage);
      document.removeEventListener("securitypolicyviolation", onViolation);
    };
  }, [origin]);

  useEffect(() => {
    if (!level.playing || hasPlayed || blocked) return;
    const timer = setTimeout(() => setHelp(true), PLAYBACK_WAIT_MS);
    return () => clearTimeout(timer);
  }, [level.playing, hasPlayed, blocked]);

  // Retrying the handshake covers slow network loads where the first listening message arrives too early.
  useEffect(() => {
    if (ready || blocked) return;
    const timer = setInterval(() => listen(frame.current, origin), 500);
    return () => clearInterval(timer);
  }, [ready, blocked, origin]);

  useEffect(() => {
    if (!ready) return;
    const target = Math.round(level.gain * 100);
    if (level.playing) command(frame.current, origin, "playVideo");
    const steps = Math.max(1, Math.round((level.seconds * 1000) / STEP_MS));
    const start = volume.current;
    let step = 0;
    const timer = setInterval(() => {
      step += 1;
      volume.current = Math.round(start + ((target - start) * step) / steps);
      command(frame.current, origin, "setVolume", [volume.current]);
      if (step >= steps) {
        clearInterval(timer);
        if (!level.playing) command(frame.current, origin, "pauseVideo");
      }
    }, STEP_MS);
    return () => clearInterval(timer);
  }, [ready, level.gain, level.seconds, level.playing, origin]);

  if (!src) return null;
  return (
    <PlayerCard title={soundscape.title} watchUrl={stream.watch_url} host="YouTube">
      {blocked ? (
        <div className="flex flex-col items-start gap-2 p-3 text-ui-sm">
          <p>{t("sound.reloadNeeded")}</p>
          <button type="button" onClick={page.reload} className={button}>
            {t("sound.reload")}
          </button>
        </div>
      ) : (
        <iframe
          ref={frame}
          title={t("sound.player", { title: soundscape.title })}
          src={src}
          onLoad={() => listen(frame.current, origin)}
          className="aspect-video min-h-[200px] w-full border-0"
          referrerPolicy={FRAME_ATTRIBUTES.referrerPolicy}
          sandbox={account ? ACCOUNT_SANDBOX : FRAME_ATTRIBUTES.sandbox}
          allow={FRAME_ATTRIBUTES.allow}
        />
      )}
      <details open={help} onToggle={(event) => setHelp(event.currentTarget.open)} className="p-2 text-ui-sm">
        <summary className="min-h-8 cursor-pointer content-center rounded-lg px-1 hover:bg-accent">
          {t("sound.playbackHelp")}
        </summary>
        <div className="flex flex-col gap-2 pt-2">
          <p>{t(account ? "sound.accountHelp" : "sound.privateHelp")}</p>
          {chooseAccount && (
            <button type="button" onClick={() => chooseAccount(!account)} className={button}>
              {t(account ? "sound.privatePlayer" : "sound.accountPlayer")}
            </button>
          )}
          <button type="button" onClick={retry} className={button}>
            {t("sound.retryPlayer")}
          </button>
          <a href={stream.watch_url} target="_blank" rel="noopener noreferrer" onClick={stop} className={button}>
            {t("sound.playOnYouTube")}
          </a>
        </div>
      </details>
    </PlayerCard>
  );
}
