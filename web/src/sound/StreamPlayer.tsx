import { Copy, ExternalLink, EyeOff, LogIn, RotateCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { SchemaSoundscapeView } from "@/api/client";
import { PaneAction } from "@/components/PaneActions";
import { RefreshGlyph } from "@/components/RefreshGlyph";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { isIOS } from "@/lib/browser";
import { useNativeSurface } from "@/lib/nativeSurface";
import { refreshName } from "@/lib/refreshFeedback";
import { PlayerCard } from "./PlayerCard";
import { PlayerHelp } from "./PlayerHelp";
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
type GuestState = { status?: string; ready?: boolean; state?: number; error?: boolean };
type GuestBridge = (request: Record<string, unknown>) => Promise<GuestState>;

/** The desktop's player guest on the website session it shares with the sidebar browser (ADR 0140), if any. */
function guestBridge(): GuestBridge | undefined {
  const value = (window as Window & { heinzelDesktop?: { player?: unknown } }).heinzelDesktop?.player;
  return typeof value === "function" ? (value as GuestBridge) : undefined;
}

/** The IFrame API command as the guest bridge's narrow request; anything else has no guest equivalent. */
export function guestRequest(func: string, args: unknown[] = []): Record<string, unknown> | null {
  if (func === "playVideo") return { action: "play" };
  if (func === "pauseVideo") return { action: "pause" };
  if (func === "mute") return { action: "volume", value: 0 };
  if (func === "setVolume" && typeof args[0] === "number")
    return { action: "volume", value: Math.max(0, Math.min(100, Math.round(args[0]))) };
  return null;
}
type Props = { soundscape: SchemaSoundscapeView; stream: StreamView; level: Level };
const STEP_MS = 100;
const PLAYBACK_WAIT_MS = 15_000;
const button = "inline-flex min-h-8 items-center gap-1 rounded-lg border border-border px-2.5 py-1 hover:bg-accent";

/** A private player by default, with explicit account playback and recovery for restricted browsers (ADR 0092). */
export function StreamPlayer(props: Props) {
  const [account, setAccount] = useState(readYouTubeAccount);
  const [attempt, setAttempt] = useState(0);
  const alternative = accountStream(props.stream);
  const desktop =
    typeof (window as Window & { heinzelDesktop?: { lifecycle?: unknown } }).heinzelDesktop?.lifecycle === "function";
  const activeAccount = !desktop && account && alternative !== null;
  const chooseAccount = (next: boolean) => {
    rememberYouTubeAccount(next);
    setAccount(next);
  };
  return (
    <Player
      key={`${account}:${attempt}:${props.stream.video}`}
      {...props}
      stream={activeAccount && alternative ? alternative : props.stream}
      account={activeAccount}
      desktop={desktop}
      copyUrl={desktop && alternative ? `https://www.youtube.com/watch?v=${props.stream.video}` : undefined}
      chooseAccount={!desktop && alternative ? chooseAccount : undefined}
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
  desktop,
  copyUrl,
}: Props & {
  account: boolean;
  desktop: boolean;
  copyUrl?: string;
  chooseAccount?: (next: boolean) => void;
  retry: () => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  // In the desktop the YouTube player is a guest on the website session, so a sign-in in the browser counts here.
  const guest = desktop && stream.provider === "youtube" ? guestBridge() : undefined;
  const [ready, setReady] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [reloadFailed, setReloadFailed] = useState(false);
  const [help, setHelp] = useState(false);
  const [guestUnavailable, setGuestUnavailable] = useState(false);
  const [hasPlayed, setHasPlayed] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const volume = useRef(0);
  const src = embedSrc(stream);
  const origin = stream.frame_origin;
  const deviceVolume = isIOS();
  const send = useCallback(
    (func: string, args: unknown[] = []) => {
      if (!guest) {
        command(frame.current, origin, func, args);
        return;
      }
      const request = guestRequest(func, args);
      if (request) void guest(request).catch(() => {});
    },
    [guest, origin],
  );
  const sawReady = useRef(false);
  const stateRequest = useRef(0);
  const appliedState = useRef(0);
  const openPending = useRef(false);
  const onGuestState = useCallback(
    (next: GuestState) => {
      if (next.status === "unavailable") setGuestUnavailable(true);
      else if (next.status === "loading" || next.status === "ready") setGuestUnavailable(false);
      if (next.status === "unavailable") setHelp(true);
      if (next.error) setHelp(true);
      if (next.state === 1) setHasPlayed(true);
      if (next.ready && !sawReady.current) {
        sawReady.current = true;
        // The guest starts silent, like the frame, so the fade decides what is heard.
        void guest?.({ action: "volume", value: volume.current }).catch(() => {});
        setReady(true);
      }
    },
    [guest],
  );
  useEffect(() => {
    if (!guest || !src) return;
    openPending.current = true;
    void guest({ action: "open", video: stream.video })
      .then(onGuestState)
      .catch(() => {
        setGuestUnavailable(true);
        setHelp(true);
      })
      .finally(() => {
        openPending.current = false;
      });
    return () => {
      void guest({ action: "close" }).catch(() => {});
    };
  }, [guest, src, stream.video, onGuestState]);
  const surfaceFailed = useNativeSurface(
    surface,
    true,
    guest,
    () => {
      if (openPending.current) return;
      const request = ++stateRequest.current;
      void guest?.({ action: "state" })
        .then((next) => {
          if (request > appliedState.current) {
            appliedState.current = request;
            onGuestState(next);
          }
        })
        .catch(() => {});
    },
    "player",
  );
  const copyLink = async () => {
    try {
      if (!copyUrl || !navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(copyUrl);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };

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
      if (deviceVolume) command(frame.current, origin, volume.current === 0 ? "mute" : "unMute");
      else command(frame.current, origin, "setVolume", [volume.current]);
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
  }, [origin, deviceVolume]);

  useEffect(() => {
    if (!level.playing || hasPlayed || blocked) return;
    const timer = setTimeout(() => setHelp(true), PLAYBACK_WAIT_MS);
    return () => clearTimeout(timer);
  }, [level.playing, hasPlayed, blocked]);

  // Retrying the handshake covers slow network loads where the first listening message arrives too early.
  useEffect(() => {
    if (ready || blocked || guest) return;
    const timer = setInterval(() => listen(frame.current, origin), 500);
    return () => clearInterval(timer);
  }, [ready, blocked, origin, guest]);

  useEffect(() => {
    if (!ready) return;
    const target = Math.round(level.gain * 100);
    // iOS owns media volume; mute and pause still work, so meetings and Pause must not depend on a fade.
    if (deviceVolume) {
      volume.current = target;
      send(target === 0 ? "mute" : "unMute");
      send(level.playing ? "playVideo" : "pauseVideo");
      return;
    }
    if (level.playing) send("playVideo");
    const steps = Math.max(1, Math.round((level.seconds * 1000) / STEP_MS));
    const start = volume.current;
    let step = 0;
    const timer = setInterval(() => {
      step += 1;
      volume.current = Math.round(start + ((target - start) * step) / steps);
      send("setVolume", [volume.current]);
      if (step >= steps) {
        clearInterval(timer);
        if (!level.playing) send("pauseVideo");
      }
    }, STEP_MS);
    return () => clearInterval(timer);
  }, [ready, level.gain, level.seconds, level.playing, send, deviceVolume]);

  if (!src) return null;
  const helpPane = (
    <PlayerHelp
      inHeader={Boolean(guest)}
      attention={help}
      actions={
        <>
          {chooseAccount && (
            <PaneAction icon={account ? EyeOff : LogIn} onClick={() => chooseAccount(!account)}>
              {t(account ? "sound.privatePlayer" : "sound.accountPlayer")}
            </PaneAction>
          )}
          {copyUrl && (
            <PaneAction icon={Copy} onClick={() => void copyLink()}>
              {t("sound.copyYouTubeLink")}
            </PaneAction>
          )}
          <PaneAction icon={RotateCw} onClick={retry}>
            {t("sound.retryPlayer")}
          </PaneAction>
          {!desktop && (
            <PaneAction icon={ExternalLink} href={stream.watch_url} onClick={stop}>
              {t("sound.playOnYouTube")}
            </PaneAction>
          )}
        </>
      }
    >
      <p>{t(desktop ? "sound.desktopHelp" : account ? "sound.accountHelp" : "sound.privateHelp")}</p>
      {copyState !== "idle" && (
        <p role="status">{t(copyState === "copied" ? "sound.linkCopied" : "sound.copyFailed")}</p>
      )}
      {copyState === "failed" && copyUrl && (
        <input
          aria-label={t("sound.videoLink")}
          className="w-full rounded border border-border bg-transparent px-1"
          readOnly
          value={copyUrl}
          onFocus={(event) => event.currentTarget.select()}
        />
      )}
    </PlayerHelp>
  );
  return (
    <PlayerCard
      title={soundscape.title}
      watchUrl={desktop ? undefined : stream.watch_url}
      host="YouTube"
      headerAction={guest ? helpPane : undefined}
    >
      <div className="group/video relative">
        {guest ? (
          <div
            ref={surface}
            role="img"
            aria-label={t("sound.player", { title: soundscape.title })}
            className="aspect-video min-h-[200px] w-full"
          />
        ) : blocked ? (
          <div className="flex flex-col items-start gap-2 p-3 text-ui-sm">
            <p>{t("sound.reloadNeeded")}</p>
            <button
              type="button"
              disabled={reloading}
              aria-busy={reloading}
              aria-label={refreshName(t("sound.reload"), reloading, reloadFailed)}
              onClick={() => {
                setReloading(true);
                setReloadFailed(false);
                try {
                  page.reload();
                } catch {
                  setReloading(false);
                  setReloadFailed(true);
                }
              }}
              className={button}
            >
              <RefreshGlyph busy={reloading} failed={reloadFailed} />
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
        {!guest && helpPane}
      </div>
      {guestUnavailable && (
        <div role="alert" className="flex items-center gap-2 p-2 text-ui-sm">
          <span>{t("sound.desktopUnavailable")}</span>
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            {t("sound.retryPlayer")}
          </Button>
        </div>
      )}
      {surfaceFailed && (
        <p role="alert" className="p-2 text-ui-sm">
          {t("browser.surfaceFailed")}
        </p>
      )}
    </PlayerCard>
  );
}
