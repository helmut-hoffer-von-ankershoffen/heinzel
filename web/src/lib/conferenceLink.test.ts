import { expect, it } from "vitest";
import { conferenceProvider } from "./conferenceLink";

it("names the meeting tool by host and nothing for unknown or unsafe links", () => {
  expect(conferenceProvider("https://acme.zoom.us/j/1")).toBe("Zoom");
  expect(conferenceProvider("https://meet.google.com/abc-defg-hij")).toBe("Google Meet");
  expect(conferenceProvider("https://teams.microsoft.com/l/meetup-join/x")).toBe("Microsoft Teams");
  expect(conferenceProvider("https://acme.webex.com/meet/x")).toBe("Webex");
  expect(conferenceProvider("https://meet.example/test")).toBeNull();
  expect(conferenceProvider("https://evilzoom.us/j/1")).toBeNull();
  expect(conferenceProvider("javascript:alert(1)")).toBeNull();
  expect(conferenceProvider(null)).toBeNull();
});
