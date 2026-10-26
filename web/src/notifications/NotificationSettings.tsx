// Settings > General: install the app, desktop notifications in this browser, and unpairing this browser.
// The browser's permission is asked only when the person turns notifications on, never on page load.
import { useEffect, useId, useState } from "react";
import { forgetDevice } from "@/api/device";
import { deviceToken } from "@/api/token";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { InstallApp } from "@/pwa/InstallApp";
import { setDesktop, useDesktopState } from "./desktop";
import { NotificationTest } from "./NotificationTest";
import { notificationSoundAvailable, notificationSoundEnabled, setNotificationSoundEnabled } from "./sound";

export function NotificationSettings() {
  const state = useDesktopState();
  const id = useId();
  const [busy, setBusy] = useState(false);
  const [paired, setPaired] = useState(deviceToken() !== null);
  const [soundOn, setSoundOn] = useState(notificationSoundEnabled);
  const [soundProblem, setSoundProblem] = useState("");
  const soundId = useId();

  useEffect(() => {
    const syncSound = (event: StorageEvent) => {
      if (event.key === "hz.notification-sound") setSoundOn(event.newValue === "on");
    };
    window.addEventListener("storage", syncSound);
    return () => window.removeEventListener("storage", syncSound);
  }, []);

  const toggle = async (on: boolean) => {
    setBusy(true);
    try {
      await setDesktop(on);
    } finally {
      setBusy(false);
    }
  };

  const toggleSound = async (on: boolean) => {
    setBusy(true);
    setSoundProblem("");
    try {
      if (await setNotificationSoundEnabled(on)) setSoundOn(on);
      else setSoundProblem(t(notificationSoundAvailable() ? "notify.sound.gesture" : "notify.sound.unsupported"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <InstallApp />
      <label htmlFor={id} className="flex items-center gap-2 text-sm">
        <input
          id={id}
          type="checkbox"
          role="switch"
          aria-checked={state === "on"}
          checked={state === "on"}
          disabled={busy || state === "unsupported" || state === "denied"}
          onChange={(event) => void toggle(event.target.checked)}
        />
        {t("notify.toggle")}
      </label>
      <p className="text-xs text-muted-foreground">{t(`notify.state.${state}`)}</p>
      <div className="flex flex-col gap-1">
        <label htmlFor={soundId} className="flex items-center gap-2 text-sm">
          <input
            id={soundId}
            type="checkbox"
            role="switch"
            aria-checked={soundOn}
            checked={soundOn}
            disabled={busy || !notificationSoundAvailable()}
            onChange={(event) => void toggleSound(event.target.checked)}
          />
          {t("notify.sound.toggle")}
        </label>
        <p className="text-xs text-muted-foreground">
          {soundProblem || t(notificationSoundAvailable() ? "notify.sound.help" : "notify.sound.unsupported")}
        </p>
      </div>
      <NotificationTest />
      {paired && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>{t("device.paired")}</span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void forgetDevice().then(() => setPaired(false))}
            title={t("device.forgetHint")}
          >
            {t("device.forget")}
          </Button>
        </div>
      )}
    </div>
  );
}
