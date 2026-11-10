export type ExternalAction = "join-meeting" | "open-calendar" | "watch-video";
export function externalActionUrl(action: ExternalAction, target: string): string | null;
