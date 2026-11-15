// Meet your first Heinzelmännchen (ADR 0157, canvas boards 17, 17b and 18): a name, the figure with its look
// editor (click a part, or use the list "Everything you can change"), Surprise me with Undo, and the first mission.
// Used by the chat's first start and by the onboarding step 7: one first start, not two.
// The request id is kept until hz confirms, so a lost answer recovers the same Heinzel instead of making a second.
import { useQueryClient } from "@tanstack/react-query";
import { Shuffle, Undo2 } from "lucide-react";
import { type FormEvent, useId, useRef, useState } from "react";
import { api, problem } from "@/api/client";
import { Button } from "@/components/ui/button";
import { LookFigure, LookParts, type PartId } from "@/heinzel/LookEditor";
import { MISSIONS } from "@/heinzel/missions";
import { type Meeting, START_LOOK, SUGGESTED_NAMES, surprise } from "@/heinzel/surprise";
import { t } from "@/i18n";
import { isDurableSession, useComposerFocus } from "@/lib/chatIntent";
import { rememberHeinzel } from "./defaultHeinzel";

const REQUEST = "hz.chat.first-heinzel";

/** The request id for this name and look: kept until hz confirms, so a retry recovers the same Heinzel. */
function requestId(key: string): string {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(REQUEST) ?? "null");
    if (stored && typeof stored === "object" && "id" in stored && "name" in stored && typeof stored.id === "string") {
      if (stored.name === key) return stored.id;
    }
    const fresh = crypto.randomUUID();
    localStorage.setItem(REQUEST, JSON.stringify({ id: fresh, name: key }));
    return fresh;
  } catch {
    return crypto.randomUUID();
  }
}

/** Forget the first-start request: hz confirmed it, or the list already shows a Heinzel. */
export function settleFirstHeinzel() {
  try {
    localStorage.removeItem(REQUEST);
  } catch {
    // Nothing to forget without storage.
  }
}

export function MeetHeinzel({ onCreated }: { onCreated: (sessionId: string) => void }) {
  const client = useQueryClient();
  const [meeting, setMeeting] = useState<Meeting>({
    name: SUGGESTED_NAMES[0],
    suggested: SUGGESTED_NAMES[0],
    look: START_LOOK,
  });
  const [earlier, setEarlier] = useState<Meeting[]>([]);
  const [part, setPart] = useState<PartId>("hat");
  const [mission, setMission] = useState<string>(MISSIONS[0]?.id ?? "day");
  const [announce, setAnnounce] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const creating = useRef(false);
  const field = useId();
  const nameInput = useRef<HTMLInputElement>(null);
  useComposerFocus(nameInput, true);
  const group = useId();
  const { name, look } = meeting;
  const chosen = MISSIONS.find((item) => item.id === mission) ?? MISSIONS[0];
  const shown = name.trim() || meeting.suggested;
  // Every change of the look or the name is one step back; typing a name is one step per burst, not per letter.
  const change = (next: Meeting) => {
    setEarlier((steps) => [...steps, meeting]);
    setMeeting(next);
  };
  const rollAgain = () => {
    const next = surprise(meeting);
    change(next);
    setAnnounce(t("meet.surprised", { name: next.name.trim() || next.suggested }));
  };
  const undo = () => {
    const previous = earlier[earlier.length - 1];
    if (!previous) return;
    setEarlier((steps) => steps.slice(0, -1));
    // The look goes back; the name only while it is still the suggestion, so Undo never eats what you typed.
    const untouched = name === meeting.suggested;
    setMeeting(untouched ? previous : { ...meeting, look: previous.look });
    setAnnounce("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (creating.current) return;
    creating.current = true;
    setBusy(true);
    setFailed(false);
    try {
      const { data, error, response } = await api.POST("/heinzel/new-chat", {
        body: { request_id: requestId(`${shown}|${JSON.stringify(look)}`), name: shown, appearance: look },
      });
      if (!response.ok || !data) throw problem(response.status, error);
      if (!isDurableSession(data.session_id)) throw new Error("Invalid creation acknowledgement");
      settleFirstHeinzel();
      rememberHeinzel(data.session_id);
      client.setQueryData(["heinzel", "session", data.session_id], data);
      await client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
      onCreated(data.session_id);
    } catch {
      setFailed(true);
      // A lost answer may still have made the Heinzel: the panel opens it as soon as the list shows it.
      void client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
    } finally {
      creating.current = false;
      setBusy(false);
    }
  };
  return (
    <form onSubmit={(event) => void submit(event)} className="flex w-full max-w-2xl flex-col gap-5 text-left">
      <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
        <LookFigure name={shown} look={look} part={part} onPart={setPart} />
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label htmlFor={field} className="text-sm font-medium">
              {t("oneChat.nameLabel")}
            </label>
            <input
              id={field}
              ref={nameInput}
              value={name}
              maxLength={100}
              autoComplete="off"
              onChange={(event) => setMeeting({ ...meeting, name: event.target.value })}
              className="h-11 rounded-lg border border-border bg-card px-3"
            />
            <p className="text-xs text-muted-foreground">{t("meet.nameHelp")}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" className="min-h-11" onClick={rollAgain}>
              <Shuffle aria-hidden />
              {t("meet.surprise")}
            </Button>
            <Button type="button" variant="ghost" className="min-h-11" disabled={earlier.length === 0} onClick={undo}>
              <Undo2 aria-hidden />
              {t("meet.undo")}
            </Button>
          </div>
          <p role="status" className="sr-only">
            {announce}
          </p>
          <LookParts look={look} part={part} onPart={setPart} onLook={(next) => change({ ...meeting, look: next })} />
        </div>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">{t("meet.mission")}</legend>
        {MISSIONS.map((item) => (
          <label
            key={item.id}
            className="flex min-h-11 cursor-pointer items-start gap-2 rounded-lg border border-border p-3 text-sm has-checked:border-primary has-checked:bg-primary/10"
          >
            <input
              type="radio"
              name={`${group}-mission`}
              checked={mission === item.id}
              onChange={() => setMission(item.id)}
              className="mt-1"
            />
            <span className="flex flex-col">
              <span className="font-medium">
                {t(item.title)}
                {item.recommended ? ` (${t("meet.missionRecommended")})` : ""}
              </span>
              <span className="text-muted-foreground">{t(item.job)}</span>
            </span>
          </label>
        ))}
        {chosen && <p className="text-sm italic">{t(chosen.motto)}</p>}
        <p className="text-xs text-muted-foreground">{t("meet.missionHelp")}</p>
      </fieldset>
      <Button type="submit" disabled={busy} className="h-11 self-start">
        {t("oneChat.start")}
      </Button>
      {failed && (
        <p role="alert" className="text-sm">
          {t("oneChat.createError")}
        </p>
      )}
      <p className="text-sm text-muted-foreground">{t("oneChat.later")}</p>
    </form>
  );
}
