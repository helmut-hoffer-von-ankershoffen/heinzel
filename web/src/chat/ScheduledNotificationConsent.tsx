import type { components } from "@/api/schema";
import { t } from "@/i18n";

type Policy = components["schemas"]["ScheduledNotificationPolicy"];

export const NOTIFICATION_DISCLOSURE =
  "Content may appear on this device. Topics are model-chosen, not verified source identities." as const;

export type NotificationMode = "topic" | "item" | "revision";
export const ITEM_DISCLOSURE =
  "Content may appear on this device. Item identities are provider-reported and not completeness guarantees." as const;
export const ITEM_SCOPE =
  "Only current-turn items count; deduplication is scoped to the approved source and contract." as const;

export function notificationPolicy(maxPer24h: number, cooldown: number, mode: NotificationMode = "topic"): Policy {
  return {
    max_per_24h: maxPer24h,
    topic_cooldown_seconds: cooldown,
    per_turn: 1,
    delivery: "local-notification",
    disclosure: mode === "topic" ? NOTIFICATION_DISCLOSURE : ITEM_DISCLOSURE,
    ...(mode === "topic" ? {} : { item_evidence: { deduplicate_by: mode, disclosure: ITEM_SCOPE } }),
  };
}

export function validNotificationPolicy(value: unknown): value is Policy {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const policy = value as Record<string, unknown>;
  const fields = Object.keys(policy).sort().join(",");
  const common =
    Number.isInteger(policy.max_per_24h) &&
    typeof policy.max_per_24h === "number" &&
    policy.max_per_24h >= 1 &&
    policy.max_per_24h <= 24 &&
    Number.isInteger(policy.topic_cooldown_seconds) &&
    typeof policy.topic_cooldown_seconds === "number" &&
    policy.topic_cooldown_seconds >= 60 &&
    policy.topic_cooldown_seconds <= 2678400 &&
    policy.per_turn === 1 &&
    policy.delivery === "local-notification";
  if (!common) return false;
  if (fields === "delivery,disclosure,max_per_24h,per_turn,topic_cooldown_seconds")
    return policy.disclosure === NOTIFICATION_DISCLOSURE;
  if (
    fields !== "delivery,disclosure,item_evidence,max_per_24h,per_turn,topic_cooldown_seconds" ||
    policy.disclosure !== ITEM_DISCLOSURE ||
    !policy.item_evidence ||
    typeof policy.item_evidence !== "object" ||
    Array.isArray(policy.item_evidence)
  )
    return false;
  const item = policy.item_evidence as Record<string, unknown>;
  return (
    Object.keys(item).sort().join(",") === "deduplicate_by,disclosure" &&
    (item.deduplicate_by === "item" || item.deduplicate_by === "revision") &&
    item.disclosure === ITEM_SCOPE
  );
}

export function sameNotificationPolicy(left: Policy | null | undefined, right: Policy | null | undefined): boolean {
  if (left == null || right == null) return left == null && right == null;
  return (
    validNotificationPolicy(left) &&
    validNotificationPolicy(right) &&
    left.max_per_24h === right.max_per_24h &&
    left.topic_cooldown_seconds === right.topic_cooldown_seconds &&
    left.disclosure === right.disclosure &&
    (left.item_evidence?.deduplicate_by ?? null) === (right.item_evidence?.deduplicate_by ?? null) &&
    (left.item_evidence?.disclosure ?? null) === (right.item_evidence?.disclosure ?? null)
  );
}

export function ScheduledNotificationConsent({
  enabled,
  maxPer24h,
  cooldown,
  disabled,
  mode,
  itemAvailable,
  revisionAvailable,
  onMode,
  onEnabled,
  onMax,
  onCooldown,
}: {
  enabled: boolean;
  maxPer24h: number;
  cooldown: number;
  disabled: boolean;
  mode: NotificationMode;
  itemAvailable: boolean;
  revisionAvailable: boolean;
  onMode: (value: NotificationMode) => void;
  onEnabled: (value: boolean) => void;
  onMax: (value: number) => void;
  onCooldown: (value: number) => void;
}) {
  return (
    <fieldset className="space-y-2 rounded border border-border p-2">
      <legend className="px-1 font-medium">Scheduled local notifications</legend>
      <label className="block">
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(event) => onEnabled(event.target.checked)}
        />{" "}
        Allow this agent to queue local notifications during these scheduled turns.
      </label>
      <p>{t("scheduledNotify.note")}</p>
      {enabled && (
        <div className="space-y-2">
          <label className="block">
            Notification deduplication
            <select
              className="block rounded border p-1"
              value={mode}
              disabled={disabled}
              onChange={(event) => onMode(event.target.value as NotificationMode)}
            >
              <option value="topic">Model-chosen topic</option>
              <option value="item" disabled={!itemAvailable}>
                Once per observed item
              </option>
              <option value="revision" disabled={!revisionAvailable}>
                Once per observed item revision
              </option>
            </select>
          </label>
          {mode === "topic" ? (
            <p>Topics are model-chosen, not verified source identities.</p>
          ) : (
            <p>Only items observed in this turn count; deduplication is scoped to the approved source and contract.</p>
          )}
          {!itemAvailable && (
            <p>Select an approved read that declares item identity to use observed-item deduplication.</p>
          )}
          <div className="flex flex-wrap gap-2">
            <label>
              Maximum attempts per rolling 24 hours
              <input
                className="block w-24 rounded border p-1"
                type="number"
                min={1}
                max={24}
                value={maxPer24h}
                disabled={disabled}
                onChange={(event) => onMax(Number(event.target.value))}
              />
            </label>
            {mode === "topic" && (
              <label>
                Same-topic cooldown (seconds)
                <input
                  className="block w-28 rounded border p-1"
                  type="number"
                  min={60}
                  max={2678400}
                  value={cooldown}
                  disabled={disabled}
                  onChange={(event) => onCooldown(Number(event.target.value))}
                />
              </label>
            )}
          </div>
        </div>
      )}
    </fieldset>
  );
}
