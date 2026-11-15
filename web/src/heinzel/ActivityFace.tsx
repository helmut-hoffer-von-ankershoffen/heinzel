// One Heinzel's face in its state: the pose from the computed activity, drawn the same in every list and header.
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { appearanceLook } from "@/heinzel/look";
import { type Activity, activityPose } from "./activity";

type Props = {
  appearance: Parameters<typeof appearanceLook>[0];
  activity?: Activity;
  /** An idle Heinzel looks awake in the chat you have open and dozes in a list. */
  awake?: boolean;
  className?: string;
};

export function ActivityFace({ appearance, activity, awake = false, className }: Props) {
  return (
    <HeinzelAvatar
      view="bust"
      {...appearanceLook(appearance)}
      state={activityPose(activity, awake)}
      className={className}
    />
  );
}
