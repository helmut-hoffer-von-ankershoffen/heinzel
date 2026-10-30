import type { components } from "@/api/schema";

type Policy = components["schemas"]["ScheduledNotificationPolicy"];

export const NOTIFICATION_DISCLOSURE =
  "Content may appear on this device. Topics are model-chosen, not verified source identities." as const;

export function notificationPolicy(maxPer24h: number, cooldown: number): Policy {
  return {
    max_per_24h: maxPer24h,
    topic_cooldown_seconds: cooldown,
    per_turn: 1,
    delivery: "local-notification",
    disclosure: NOTIFICATION_DISCLOSURE,
  };
}

export function validNotificationPolicy(value: unknown): value is Policy {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const policy = value as Record<string, unknown>;
  return (
    Object.keys(policy).sort().join(",") === "delivery,disclosure,max_per_24h,per_turn,topic_cooldown_seconds" &&
    Number.isInteger(policy.max_per_24h) &&
    typeof policy.max_per_24h === "number" &&
    policy.max_per_24h >= 1 &&
    policy.max_per_24h <= 24 &&
    Number.isInteger(policy.topic_cooldown_seconds) &&
    typeof policy.topic_cooldown_seconds === "number" &&
    policy.topic_cooldown_seconds >= 60 &&
    policy.topic_cooldown_seconds <= 2678400 &&
    policy.per_turn === 1 &&
    policy.delivery === "local-notification" &&
    policy.disclosure === NOTIFICATION_DISCLOSURE
  );
}

export function sameNotificationPolicy(left: Policy | null | undefined, right: Policy | null | undefined): boolean {
  if (left == null || right == null) return left == null && right == null;
  return (
    validNotificationPolicy(left) &&
    validNotificationPolicy(right) &&
    left.max_per_24h === right.max_per_24h &&
    left.topic_cooldown_seconds === right.topic_cooldown_seconds
  );
}

export function ScheduledNotificationConsent({
  enabled,
  maxPer24h,
  cooldown,
  disabled,
  onEnabled,
  onMax,
  onCooldown,
}: {
  enabled: boolean;
  maxPer24h: number;
  cooldown: number;
  disabled: boolean;
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
      <p>
        Optional and off by default. At most one attempt per turn. Content may appear on this device; queued does not
        mean shown. Topics are model-chosen, not verified source identities.
      </p>
      {enabled && (
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
        </div>
      )}
    </fieldset>
  );
}
