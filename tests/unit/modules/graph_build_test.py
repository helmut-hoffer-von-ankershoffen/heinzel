"""Promise states, deadlines, weeks, the waiting list and cuts of the team graph, from promises and entries alone."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heinzel import HeinzelError, TeamPromise, TeamPromises, TelemetryEntry, TelemetryLog, TelemetryRead
from heinzel.modules.graph import GraphService, GraphSettings, HzGraph
from heinzel.modules.graph._build import Builder, Inputs, _label, _whole, cut, worst
from heinzel.modules.graph._models import Filters
from heinzel.modules.graph._schedule import local_zone, next_run, runs

pytestmark = pytest.mark.unit

LISBON = ZoneInfo("Europe/Lisbon")
WEDNESDAY = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)  # 12:00 in Lisbon, the row's run
FRIDAY = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)  # 17:00 in Lisbon, the report's run
ROW = TeamPromise(
    contract="team:row", title="Row", version="1.0.0", schedule="0 12 * * WED", writes="contract:team/row#row"
)
REPORT = TeamPromise(
    contract="team:report",
    title="Report",
    version="1.0.0",
    schedule="0 17 * * FRI",
    writes="contract:team/report#report",
    reads=("contract:team/row#row",),
    publishes=("teamwork.send",),
)


def entry(verb: str, time: datetime, heinzel: str = "costas-row", **fields: object) -> TelemetryEntry:
    defaults: dict[str, object] = {"contract": "team:row"}
    if verb != "heartbeat":
        defaults["target"] = "https://tracker.acme.example/7"
    return TelemetryEntry.model_validate(
        {"heinzel_id": heinzel, "user": heinzel.partition("-")[0], "time": time, "verb": verb} | defaults | fields
    )


def graph(*entries: TelemetryEntry, at: datetime, promises=(ROW, REPORT), weeks: int = 2) -> dict:
    inputs = Inputs(
        promises=promises,
        entries=entries,
        as_of=at,
        zone=LISBON,
        zone_name="Europe/Lisbon",
        offline_after=timedelta(days=2),
        weeks=weeks,
        problems=("a skipped line",),
    )
    return Builder(inputs).build().model_dump(mode="json")["graph"]


def state(document: dict, node: str = "heinzel:costas-row") -> str | None:
    return document["nodes"][node]["metadata"]["state"]


def test_a_row_is_due_by_the_next_run_of_the_report_that_reads_it():
    document = graph(entry("heartbeat", WEDNESDAY), at=WEDNESDAY + timedelta(hours=1))
    assert document["nodes"]["heinzel:costas-row"]["metadata"]["deadline"] == "2026-10-23T16:00:00Z"
    assert state(document) == "on_time"  # on track: nothing drafted yet, the deadline is ahead


def test_a_promise_nobody_reads_is_due_by_its_own_next_run():
    document = graph(entry("heartbeat", FRIDAY, "mira-report", contract="team:report"), at=FRIDAY + timedelta(hours=1))
    assert document["nodes"]["heinzel:mira-report"]["metadata"]["deadline"] == "2026-10-30T17:00:00Z"


@pytest.mark.parametrize(
    ("entries", "hours", "expected"),
    [
        ([("drafted", 1)], 40, "waiting_for_approval"),
        ([("drafted", 1), ("approved", 2)], 50, "on_time"),  # the row publishes nothing: approved is kept
        ([("drafted", 1), ("fell_back", 2)], 50, "fell_back"),
        ([("drafted", 1), ("fell_back", 2), ("approved", 3)], 70, "fell_back"),
        ([("approved", 60)], 70, "late"),  # approved after Friday's run
        ([("drafted", 1)], 70, "late"),  # never approved, and Friday's run has passed
    ],
)
def test_each_state_of_a_kept_or_pending_promise(entries, hours, expected):
    extra = {"approved": {"by": "costas"}, "fell_back": {"input": "contract:team/x#y"}}
    reported = [entry(verb, WEDNESDAY + timedelta(hours=h), **extra.get(verb, {})) for verb, h in entries]
    assert state(graph(*reported, at=WEDNESDAY + timedelta(hours=hours))) == expected


def test_silent_for_too_long_is_offline_and_past_the_deadline_is_late():
    beat = entry("heartbeat", WEDNESDAY - timedelta(days=3))
    assert state(graph(beat, at=WEDNESDAY + timedelta(hours=2))) == "offline"
    assert state(graph(beat, at=FRIDAY + timedelta(hours=1))) == "late"


def test_a_report_not_kept_by_its_own_next_run_is_late():
    beat = entry("heartbeat", FRIDAY + timedelta(hours=1), "mira-report", contract="team:report")
    document = graph(beat, at=FRIDAY + timedelta(days=7, hours=2), promises=(REPORT,))  # after 17:00, winter time
    assert sum(w["late"] for w in document["metadata"]["weeks"]) == 1
    assert state(document, "heinzel:mira-report") == "offline"  # silent for a week


def test_runs_before_a_heinzels_first_entry_are_not_its_promises():
    document = graph(entry("heartbeat", FRIDAY), at=FRIDAY + timedelta(hours=1), weeks=4)
    assert sum(w["due"] for w in document["metadata"]["weeks"]) == 1  # this week's run only, late: never approved


def test_weeks_count_decided_promises_by_their_deadline():
    reported = [
        entry("drafted", WEDNESDAY - timedelta(days=7, minutes=-5)),
        entry("approved", WEDNESDAY - timedelta(days=7, minutes=-30), by="costas"),
        entry("heartbeat", WEDNESDAY + timedelta(hours=1)),
    ]
    weeks = {w["week"]: w for w in graph(*reported, at=FRIDAY + timedelta(hours=1))["metadata"]["weeks"]}
    assert weeks["2026-W42"] == {"week": "2026-W42", "due": 1, "on_time": 1, "fell_back": 0, "late": 0}
    assert weeks["2026-W43"]["late"] == 1  # this week's row was never approved


def test_the_flow_waits_where_a_runs_inputs_are_not_updated():
    reported = [
        entry("drafted", WEDNESDAY + timedelta(minutes=5), part="costas"),
        entry("drafted", WEDNESDAY + timedelta(minutes=5), "priya-row", part="priya"),
        entry("approved", WEDNESDAY + timedelta(hours=1), "priya-row", part="priya", by="priya"),
        entry("heartbeat", WEDNESDAY + timedelta(hours=2), "mira-report", contract="team:report"),
    ]
    document = graph(*reported, at=WEDNESDAY + timedelta(hours=3))
    (waiting,) = document["metadata"]["waiting"]
    assert waiting == {
        "artifact": "contract:team/report#report",
        "summary": "Report waiting on 1 update",
        "on": ["contract:team/row#row/costas"],
    }
    assert document["metadata"]["summary"] == "Report waiting on 1 update"
    row = document["nodes"]["contract:team/row#row"]["metadata"]
    assert (row["state"], row["owner"]) == ("waiting_for_approval", None)  # the worst of two rows, kept by two
    assert document["nodes"]["contract:team/row#row/priya"]["metadata"]["parent"] == "contract:team/row#row"
    assert document["metadata"]["problems"] == ["a skipped line"]


def test_nothing_waits_once_the_inputs_are_kept():
    approved = entry("approved", WEDNESDAY + timedelta(hours=1), by="costas")
    document = graph(approved, at=WEDNESDAY + timedelta(hours=2))
    assert (document["metadata"]["waiting"], document["metadata"]["summary"]) == ([], "Nothing waits")


def test_an_unkept_promise_shows_a_heinzel_named_after_its_contract():
    document = graph(at=WEDNESDAY + timedelta(hours=1))
    node = document["nodes"]["heinzel:team:row"]
    assert (node["label"], node["metadata"]["owner"], node["metadata"]["state"]) == ("Row", None, "offline")
    assert document["metadata"]["waiting"][0]["on"] == ["contract:team/row#row"]


def test_observed_reads_and_writes_are_edges_beside_the_declared_ones():
    reported = [
        entry("read", WEDNESDAY, "mira-report", contract="team:report", target="https://tracker.acme.example/7",
              version="r3", needed_by=FRIDAY),
        entry("drafted", WEDNESDAY, part="costas", target="https://tracker.acme.example/7"),
        entry("published", WEDNESDAY, "theo-notes", contract=None, target="chat:#payments", version="v1"),
        entry("published", WEDNESDAY + timedelta(hours=1), "theo-notes", contract=None, target="chat:#payments"),
        entry("fell_back", WEDNESDAY, "mira-report", contract="team:report", input="https://docs.acme.example/old"),
    ]  # fmt: skip
    document = graph(*reported, at=WEDNESDAY + timedelta(hours=2))
    edges = {(e["source"], e["relation"], e["target"]): e["metadata"] for e in document["edges"]}
    read = edges["heinzel:mira-report", "reads", "contract:team/row#row"]  # the row's place is the tracker URL
    assert (read["declared"], read["observed"], read["last_version"]) == (True, True, "r3")
    assert read["needed_by"] == "2026-10-23T16:00:00Z"
    notes = edges["heinzel:theo-notes", "writes", "chat:#payments"]
    assert (notes["declared"], notes["observed"], notes["last_version"], notes["state"]) == (False, True, "v1", None)
    stale = edges["heinzel:mira-report", "reads", "https://docs.acme.example/old"]
    assert (stale["declared"], stale["observed"]) == (False, True)
    assert document["nodes"]["https://docs.acme.example/old"]["label"] == "docs.acme.example/old"
    assert document["nodes"]["heinzel:theo-notes"]["metadata"]["state"] is None  # no team contract: no promise


def test_entries_after_the_graphs_time_and_unscheduled_contracts_promise_nothing():
    manual = TeamPromise(contract="team:row", title="Row", version="1", schedule=None, writes="contract:team/row#row")
    document = graph(entry("drafted", FRIDAY), at=WEDNESDAY + timedelta(hours=1), promises=(manual,))
    node = document["nodes"]["heinzel:team:row"]["metadata"]
    assert (node["state"], node["deadline"]) == ("offline", None)


def test_worst_labels_and_parts():
    assert worst(["on_time", None, "late", "offline"]) == "late"
    assert worst([None]) is None
    assert _label("contract:team/row#row") == "row row"
    assert _label("team-payments@acme.example") == "team-payments@acme.example"
    assert _whole("contract:team/row#row/priya") == "contract:team/row#row"
    assert _whole("https://x.example/a/b") == "https://x.example/a/b"


def test_a_cut_keeps_the_selection_its_edges_their_ends_and_parents():
    reported = [entry("drafted", WEDNESDAY, part="costas"), entry("heartbeat", WEDNESDAY, "mira-report",
                                                                  contract="team:report")]  # fmt: skip
    inputs = Inputs((ROW, REPORT), reported, WEDNESDAY + timedelta(hours=1), LISBON, "Europe/Lisbon", timedelta(2), 2)
    document = Builder(inputs).build()
    cut_down = cut(document, Filters(artifact="contract:team/report#report", heinzel=None, state=None))
    assert set(cut_down.graph.nodes) == {"contract:team/report#report", "heinzel:mira-report"}
    assert cut_down.graph.metadata.filters.artifact == "contract:team/report#report"
    everything = cut(document, Filters(artifact=None, heinzel=None, state=None))
    assert everything.graph.nodes == document.graph.nodes


class Promises(TeamPromises):
    def promises(self) -> tuple[TeamPromise, ...]:
        return (ROW,)


class Log(TelemetryLog):
    def entries(self) -> TelemetryRead:
        return TelemetryRead(entries=(entry("heartbeat", WEDNESDAY),))


class Kernel:
    def __init__(self, *, provided: bool = True) -> None:
        self.provided = provided

    def provides(self, kind: type) -> bool:
        return self.provided

    def resolve(self, kind: type) -> object:
        return Promises() if kind is TeamPromises else Log()


def service(time_zone: str | None = "Europe/Lisbon", *, provided: bool = True) -> GraphService:
    settings = GraphSettings(time_zone=time_zone)
    clock = lambda: WEDNESDAY + timedelta(hours=1)  # noqa: E731 - a fixed clock
    zone = lambda: (UTC, "UTC")  # noqa: E731 - a fixed system zone
    return GraphService(Kernel(provided=provided), settings, clock=clock, zone=zone)  # type: ignore[arg-type]


def test_the_service_reads_promises_and_telemetry_through_the_kernel():
    document = service().graph()
    assert set(document.graph.nodes) == {"heinzel:costas-row", "contract:team/row#row"}
    assert document.graph.metadata.time_zone == "Europe/Lisbon"
    assert service(None).graph().graph.metadata.time_zone == "UTC"
    assert service(provided=False).graph().graph.nodes == {}
    assert HzGraph(service()).snapshot()["graph"]["type"] == "hz-team"


def test_the_service_refuses_an_unknown_zone_a_naive_time_and_unknown_filters():
    with pytest.raises(HeinzelError, match="isn't an IANA time zone"):
        service("Mars/Olympus").graph()
    with pytest.raises(HeinzelError, match="needs an offset"):
        service().graph(at=datetime(2026, 10, 21, 12, 0))  # noqa: DTZ001 - a naive time is the point
    with pytest.raises(
        HeinzelError, match="No artifact 'nope' in your team's graph; the artifacts: contract:team/row#row"
    ):
        service().graph(artifact="nope")
    assert set(service().graph(heinzel="costas-row").graph.nodes) == {"heinzel:costas-row", "contract:team/row#row"}


def test_the_zone_is_tz_else_the_zone_file_localtime_points_at(tmp_path: Path, monkeypatch):
    zoneinfo = tmp_path / "zoneinfo" / "Europe"
    zoneinfo.mkdir(parents=True)
    (zoneinfo / "Lisbon").write_bytes(b"")
    (tmp_path / "localtime").symlink_to(zoneinfo / "Lisbon")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert local_zone(tmp_path / "localtime") == (ZoneInfo("Asia/Tokyo"), "Asia/Tokyo")
    monkeypatch.setenv("TZ", "Not/AZone")
    assert local_zone(tmp_path / "localtime") == (LISBON, "Europe/Lisbon")
    monkeypatch.delenv("TZ")
    (zoneinfo / "Nowhere").write_bytes(b"")
    (tmp_path / "unknown").symlink_to(zoneinfo / "Nowhere")
    copy = tmp_path / "copy"
    copy.write_bytes(Path("/usr/share/zoneinfo/Europe/Lisbon").read_bytes())
    zone, name = local_zone(copy)  # a copied zone file, as Linux images often have
    assert name == "localtime"
    assert zone.utcoffset(datetime(2026, 7, 1)) == timedelta(hours=1)  # noqa: DTZ001 - wall-clock arithmetic
    _, name = local_zone(tmp_path / "unknown")  # a broken zone file: the current fixed offset
    assert name


def test_runs_follow_the_daemons_daylight_saving_rules():
    berlin = ZoneInfo("Europe/Berlin")
    spring = runs("30 2 * * *", datetime(2026, 3, 28, 12, tzinfo=UTC), datetime(2026, 3, 29, 12, tzinfo=UTC), berlin)
    assert [r.isoformat() for r in spring] == ["2026-03-29T03:30:00+02:00"]  # shifted by the gap
    autumn = runs("30 2 * * *", datetime(2026, 10, 24, 12, tzinfo=UTC), datetime(2026, 10, 25, 12, tzinfo=UTC), berlin)
    assert [r.isoformat() for r in autumn] == ["2026-10-25T02:30:00+02:00"]  # once, at its first occurrence
    assert (
        next_run("30 2 * * *", datetime(2026, 3, 28, 12, tzinfo=UTC), berlin).isoformat() == "2026-03-29T03:30:00+02:00"
    )


def test_a_busy_schedule_reaches_now_and_keeps_its_newest_runs():
    hourly = runs("0 * * * *", FRIDAY - timedelta(weeks=26), FRIDAY, UTC, limit=10)
    assert hourly[-1] == FRIDAY
    assert len(hourly) == 10
    reported = [entry("approved", FRIDAY - timedelta(minutes=30), by="costas", contract="team:hourly")]
    every_hour = TeamPromise(
        contract="team:hourly", title="Hourly", version="1", schedule="0 * * * *", writes="contract:team/hourly#x"
    )
    document = graph(*reported, at=FRIDAY - timedelta(minutes=10), promises=(every_hour,), weeks=26)
    assert document["nodes"]["heinzel:costas-row"]["metadata"]["state"] == "on_time"


def test_an_id_two_users_report_and_a_second_contract_are_skipped_and_named():
    reported = [
        entry("heartbeat", WEDNESDAY),
        entry("approved", WEDNESDAY + timedelta(minutes=5), heinzel="costas-row", user="priya", by="priya"),
        entry("heartbeat", WEDNESDAY + timedelta(minutes=6), contract="team:report"),
    ]
    document = graph(*reported, at=WEDNESDAY + timedelta(hours=1))
    assert document["nodes"]["heinzel:costas-row"]["metadata"]["owner"] == "costas"
    assert document["nodes"]["heinzel:costas-row"]["metadata"]["state"] == "on_time"  # priya's approval didn't count
    assert "costas-row: an entry of priya for costas's Heinzelmännchen, skipped" in document["metadata"]["problems"]
    assert any("a Heinzelmännchen keeps one contract" in p for p in document["metadata"]["problems"])


def test_offline_after_is_a_duration():
    with pytest.raises(ValueError, match="positive whole number"):
        GraphSettings(offline_after="two days")
