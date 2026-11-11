import { describe, expect, it } from "vitest";
import type { SchemaHealthReport, SchemaJobInfo, SchemaNoticeList, SchemaProfile } from "@/api/client";
import { doctorCards, eventCards, jobCards, profileCards } from "./today";

const report = (status: string, checks: Record<string, { name: string; status: string }[]>) =>
  ({ status, checks }) as unknown as SchemaHealthReport;

describe("The Today cards from the doctor's report", () => {
  it("has none while everything is up or nothing is read yet", () => {
    expect(doctorCards(undefined)).toEqual([]);
    expect(doctorCards(report("up", {}))).toEqual([]);
  });

  it("names the failing checks and calls a down report bad, a degraded one a warning", () => {
    const checks = {
      web: [
        { name: "port", status: "down" },
        { name: "ok", status: "up" },
      ],
    };
    const [down] = doctorCards(report("down", checks));
    const [degraded] = doctorCards(report("degraded", checks));
    expect(down).toMatchObject({ tone: "bad", detail: "web port", route: "/doctor", context: "route:/doctor" });
    expect(down?.title).toContain("1");
    expect(degraded?.tone).toBe("warn");
  });

  it("shortens a long list of failing checks to three and an ellipsis", () => {
    const checks = { m: ["a", "b", "c", "d"].map((name) => ({ name, status: "down" })) };
    expect(doctorCards(report("down", checks))[0]?.detail).toBe("m a, m b, m c…");
  });

  it("changes the id when what fails changes, so a dismissed card comes back", () => {
    const one = doctorCards(report("down", { m: [{ name: "a", status: "down" }] }))[0]?.id;
    const two = doctorCards(report("down", { m: [{ name: "b", status: "down" }] }))[0]?.id;
    expect(one).not.toBe(two);
  });
});

describe("The Today cards from the daemon's jobs", () => {
  it("shows only jobs that failed or have a problem", () => {
    const jobs = [
      { job: "fine", last_disposition: "ok" },
      { job: "broke", last_disposition: "failed", last_as_of: "2026-11-10T10:00:00Z" },
      { job: "stuck", last_disposition: "ok", problem: "no schedule" },
    ] as unknown as SchemaJobInfo[];
    const cards = jobCards(jobs);
    expect(cards.map((card) => card.id.split(":")[1])).toEqual(["broke", "stuck"]);
    expect(cards[1]?.detail).toBe("no schedule");
    expect(cards[0]?.detail).toMatch(/^Last run /);
    expect(cards[0]).toMatchObject({ tone: "bad", route: "/contracts", context: "text:the daemon job broke" });
  });

  it("has none without jobs", () => {
    expect(jobCards(undefined)).toEqual([]);
  });
});

describe("The Today card from your profile", () => {
  it("asks to finish an incomplete profile and says what is missing", () => {
    const card = profileCards({ complete: false, missing: ["name", "city"] } as unknown as SchemaProfile)[0];
    expect(card).toMatchObject({ tone: "info", route: "/profile", id: "profile:name,city" });
    expect(card?.detail).toContain("name, city");
  });

  it("has none for a complete or unread profile", () => {
    expect(profileCards({ complete: true, missing: [] } as unknown as SchemaProfile)).toEqual([]);
    expect(profileCards(undefined)).toEqual([]);
  });
});

describe("The Today cards from notices", () => {
  const now = Date.parse("2026-11-11T12:00:00Z");
  const notice = (id: string, at: string, kind = "info") => ({ id, at, kind, title: id, body: "b", route: "/x" });
  const list = (...notices: ReturnType<typeof notice>[]) => ({ notices }) as unknown as SchemaNoticeList;

  it("keeps only the notices of the last day", () => {
    const cards = eventCards(list(notice("new", "2026-11-11T11:00:00Z"), notice("old", "2026-11-10T11:00:00Z")), now);
    expect(cards.map((card) => card.id)).toEqual(["event:new"]);
  });

  it("shows at most five and gives each its event as chat context", () => {
    const many = Array.from({ length: 8 }, (_, i) => notice(`n${i}`, "2026-11-11T11:00:00Z"));
    const cards = eventCards(list(...many), now);
    expect(cards).toHaveLength(5);
    expect(cards[0]).toMatchObject({ context: "event:n0", detail: "b", route: "/x" });
  });

  it("calls failures bad, doctor notices a warning and the rest info", () => {
    const tones = eventCards(
      list(
        notice("a", "2026-11-11T11:00:00Z", "failed"),
        notice("b", "2026-11-11T11:00:00Z", "doctor"),
        notice("c", "2026-11-11T11:00:00Z", "draft"),
      ),
      now,
    ).map((card) => card.tone);
    expect(tones).toEqual(["bad", "warn", "info"]);
  });

  it("has none when the notify module is off", () => {
    expect(eventCards(undefined, now)).toEqual([]);
  });
});
