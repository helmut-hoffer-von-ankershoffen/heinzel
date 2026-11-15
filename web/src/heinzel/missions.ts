// The first mission a new Heinzel can start with (canvas board 18): a one-line job in the first person and a motto.
// "Your day" is the recommended one. Until a contract template exists (bead agqz, slice 3 data note) the mission is
// words only: the Heinzel starts with it proposed, never acting before you approve.
import type { MessageKey } from "@/i18n";

export type Mission = { id: string; title: MessageKey; job: MessageKey; motto: MessageKey; recommended?: boolean };

export const MISSIONS: readonly Mission[] = [
  {
    id: "day",
    title: "meet.mission.day",
    job: "meet.mission.day.job",
    motto: "meet.mission.day.motto",
    recommended: true,
  },
  {
    id: "reading",
    title: "meet.mission.reading",
    job: "meet.mission.reading.job",
    motto: "meet.mission.reading.motto",
  },
  {
    id: "projects",
    title: "meet.mission.projects",
    job: "meet.mission.projects.job",
    motto: "meet.mission.projects.motto",
  },
];
