from datetime import timedelta

import pytest

from heinzel import (
    CatchUp,
    Check,
    HealthLet,
    InfoLet,
    JobLet,
    Module,
    ModuleSettings,
    OperationLet,
    ProviderLet,
    RequirementLet,
    ServiceLet,
    SetupLet,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", ["Example", "1st", "with_underscore", "", "-dash"])
def test_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="must match"):
        Module(name, "x")


def test_operation_decorator_forms():
    module = Module("example", "x")

    @module.operation
    def say_hello() -> str:
        """Say hello.

        Longer description.
        """
        return "hi"

    @module.operation(name="custom", read_only=False)
    def _other() -> None:
        """Do something."""

    first, second = module.lets
    assert isinstance(first, OperationLet)
    assert (first.name, first.summary, first.read_only, first.func) == ("say-hello", "Say hello.", True, say_hello)
    assert isinstance(second, OperationLet)
    assert (second.name, second.read_only) == ("custom", False)


def test_operation_requires_docstring():
    module = Module("example", "x")
    with pytest.raises(ValueError, match="needs a docstring"):
        module.operation(lambda: None)


def test_rejects_duplicate_lets_of_same_kind():
    module = Module("example", "x")
    module.add(OperationLet(name="a", func=print, summary="a"))
    with pytest.raises(ValueError, match="already has"):

        @module.operation(name="a")
        def again() -> None:
            """Again."""


def test_service_and_info():
    class Service:
        pass

    module = Module("example", "x")
    module.service(Service)

    @module.info
    def section() -> dict[str, str]:
        return {"a": "b"}

    service, info = module.lets
    assert service == ServiceLet(name="Service", provides=Service, factory=Service)
    assert info == InfoLet(name="example", provider=section)
    assert module.heinzel_lets() == module.lets
    assert "example" in repr(module)


def test_credential_declares_a_let_and_returns_it():
    module = Module("example", "x")
    token = module.credential("api-token", purpose="Example API token.", service="shared")
    assert module.lets == (token,)
    assert (token.name, token.purpose, token.service) == ("api-token", "Example API token.", "shared")


@pytest.mark.parametrize(("name", "purpose", "problem"), [("API_TOKEN", "x", "must match"), ("token", " ", "purpose")])
def test_credential_needs_a_valid_name_and_a_purpose(name, purpose, problem):
    with pytest.raises(ValueError, match=problem):
        Module("example", "x").credential(name, purpose=purpose)


@pytest.mark.parametrize("service", ["GitHub", "a..b", ".a", "a/b", "a.", ""])
def test_credential_service_must_be_a_qualified_module_name(service):
    with pytest.raises(ValueError, match="qualified module name"):
        Module("example", "x").credential("token", purpose="x", service=service)


def test_credential_service_may_be_nested():
    assert Module("example", "x").credential("token", purpose="x", service="system.credential").service


def test_job_decorator_records_schedule_and_policies():
    module = Module("example", "x")

    def key(slot):
        return slot.date().isoformat()

    @module.job(schedule="0 9 * * MON", timeout="2m", retries=2, catch_up="all", idempotency=key)
    def weekly_digest() -> str:
        """Draft the weekly digest.

        More detail.
        """
        return "draft"

    [job] = module.lets
    assert isinstance(job, JobLet)
    assert (job.name, job.summary, job.schedule, job.scope) == (
        "weekly-digest",
        "Draft the weekly digest.",
        "0 9 * * MON",
        "personal",
    )
    assert (job.timeout, job.retries, job.catch_up, job.idempotency) == (timedelta(minutes=2), 2, CatchUp.ALL, key)
    assert job.func is weekly_digest


def test_job_defaults():
    module = Module("example", "x")

    @module.job(schedule="@daily", name="custom")
    def _job() -> None:
        """Do it daily."""

    [job] = module.lets
    assert isinstance(job, JobLet)
    assert (job.name, job.timeout, job.retries, job.catch_up, job.idempotency) == (
        "custom",
        timedelta(minutes=15),
        0,
        CatchUp.LATEST,
        None,
    )


def test_job_requires_docstring_and_valid_options():
    module = Module("example", "x")
    with pytest.raises(ValueError, match="needs a docstring"):
        module.job(schedule="@daily")(lambda: None)
    with pytest.raises(ValueError, match="positive whole number"):
        module.job(schedule="@daily", timeout="soon")


def _undecorated() -> None:
    """Do it."""


@pytest.mark.parametrize(
    ("options", "problem"),
    [
        ({"schedule": "@daily", "retries": -1}, "retries must be 0 or more"),
        ({"schedule": "@daily", "name": "Weekly_Digest"}, "must match"),
        ({"schedule": "@reboot"}, "five cron fields"),
        ({"schedule": "0 9 * *"}, "five cron fields"),
        ({"schedule": "0 9 * * MON; rm"}, "five cron fields"),
    ],
)
def test_jobs_are_validated_when_declared(options, problem):
    with pytest.raises(ValueError, match=problem):
        Module("example", "x").job(**options)(_undecorated)


def test_a_job_let_refuses_a_timeout_that_is_not_positive():
    with pytest.raises(ValueError, match="timeout must be positive"):
        JobLet(name="x", func=_undecorated, summary="x", schedule="@daily", timeout=timedelta(0))


def test_module_requirements_become_requirement_lets_named_after_the_capability():
    module = Module("digest", "x", requires=["tracker:read", "documents:read", "acme:code-host:read"])
    lets = [let for let in module.lets if isinstance(let, RequirementLet)]
    assert [(let.name, str(let.requirement)) for let in lets] == [
        ("tracker", "tracker:read"),
        ("documents", "documents:read"),
        ("code-host", "acme:code-host:read"),
    ]


def test_a_module_cannot_require_write_for_all_its_functions():
    with pytest.raises(ValueError, match="can't require documents:read,write: declare write on the publish operation"):
        Module("digest", "x", requires=["documents:read,write"])
    with pytest.raises(ValueError, match="can't require chat:write: declare write on the publish operation"):
        Module("announcer", "x", requires=["chat:write"])


def test_only_calendar_chat_documents_mail_and_tracker_have_a_write_mode_to_declare():
    for capability in ("code-host", "directory", "meetings"):
        with pytest.raises(ValueError, match=f"{capability} has no write mode"):
            Module("x", "x", requires=[f"{capability}:write"])


@pytest.mark.parametrize("name", ["hz", "capabilities"])
def test_names_of_kernel_configuration_tables_are_reserved(name):
    with pytest.raises(ValueError, match=f"Module name '{name}' is reserved"):
        Module(name, "x")


@pytest.mark.parametrize("name", ["setup", "join"])
def test_top_level_commands_are_reserved(name):
    with pytest.raises(ValueError, match=f"Module name '{name}' is reserved: it is the command `hz {name}`"):
        Module(name, "x")


def test_setup_decorator_forms():
    bare, optional, required = Module("a", "x"), Module("b", "x"), Module("c", "x")

    def step() -> None:
        """Pick the zones.

        More detail.
        """

    assert bare.setup(step) is step
    assert optional.setup()(step) is step
    required.setup(required=True)(step)
    assert bare.lets == (SetupLet(name="a", func=step, summary="Pick the zones.", required=False),)
    assert optional.lets[0] == SetupLet(name="b", func=step, summary="Pick the zones.")
    assert required.lets[0] == SetupLet(name="c", func=step, summary="Pick the zones.", required=True)
    assert required.lets[0].phase == "connect"


def test_a_setup_step_declares_its_phase():
    module = Module("profile", "x")

    def step() -> None:
        """Who you are."""

    module.setup(phase="identity")(step)
    assert module.lets[0].phase == "identity"
    with pytest.raises(ValueError, match="Setup phase 'later' is not one of identity, membership, connect, work, run"):
        Module("other", "x").setup(phase="later")  # type: ignore[call-overload]


def test_setup_needs_a_docstring_and_is_one_per_module():
    module = Module("clock", "x")
    with pytest.raises(ValueError, match="needs a docstring"):
        module.setup(lambda: None)

    def step() -> None:
        """Set up."""

    module.setup(step)
    with pytest.raises(ValueError, match="already has a SetupLet named 'clock'"):
        module.setup(step)


def test_a_setup_step_leaves_the_operation_names_free():
    """The step runs as `hz setup <module>`, never as a command of the module, so `setup` is an ordinary name."""

    def setup() -> None:
        """Set up."""

    module = Module("a", "x")
    module.setup(setup)
    module.operation(setup)
    assert [type(let).__name__ for let in module.lets] == ["SetupLet", "OperationLet"]


def test_a_function_cannot_pin_another_adapter_than_its_module():
    module = Module("digest", "x", requires=["acme:documents:read"])

    def publish() -> None:
        """Publish."""

    with pytest.raises(ValueError, match=r"conflicts with the pin of module 'digest' for documents \(acme\)"):
        module.operation(read_only=False, requires=["documents:write"])(publish)
    with pytest.raises(ValueError, match=r"conflicts with the pin of module 'digest' for documents \(acme\)"):
        module.job(schedule="@daily", requires=["other:documents:read"])(publish)
    module.operation(read_only=False, requires=["acme:documents:write"])(publish)
    unpinned = Module("plain", "x", requires=["documents:read"])
    with pytest.raises(ValueError, match=r"conflicts with the pin of module 'plain' for documents \(none\)"):
        unpinned.operation(read_only=False, requires=["acme:documents:write"])(publish)


def test_operations_and_jobs_record_their_requirements():
    module = Module("digest", "x")

    @module.operation(requires=["tracker:read"])
    def show() -> None:
        """Show."""

    @module.operation(read_only=False, requires=["documents:read,write"])
    def publish() -> None:
        """Publish."""

    @module.job(schedule="@daily", requires=["code-host:read"])
    def nightly() -> None:
        """Nightly."""

    requires = {
        let.name: [str(r) for r in let.requires] for let in module.lets if isinstance(let, OperationLet | JobLet)
    }
    assert requires == {"show": ["tracker:read"], "publish": ["documents:read,write"], "nightly": ["code-host:read"]}


def test_writes_are_declared_only_by_publish_operations():
    module = Module("digest", "x")

    def peek() -> None:
        """Peek."""

    with pytest.raises(ValueError, match="writes happen only in publish operations"):
        module.operation(requires=["documents:write"])(peek)
    with pytest.raises(ValueError, match="writes happen only in publish operations"):
        module.job(schedule="@daily", requires=["documents:read,write"])(peek)


def test_provides_records_an_adapter_named_after_the_module_by_default():
    module = Module("acme", "x")

    @module.provides("tracker", modes={"read"}, hosts=["api.acme.invalid"])
    class AcmeTracker:
        pass

    @module.provides("documents", modes={"read", "write"}, name="acme-docs")
    class AcmeDocuments:
        pass

    lets = [let for let in module.lets if isinstance(let, ProviderLet)]
    assert [(let.name, let.adapter, let.capability, sorted(let.modes), let.hosts) for let in lets] == [
        ("acme:tracker", "acme", "tracker", ["read"], ("api.acme.invalid",)),
        ("acme-docs:documents", "acme-docs", "documents", ["read", "write"], ()),
    ]
    assert lets[0].factory is AcmeTracker
    assert lets[1].factory is AcmeDocuments


@pytest.mark.parametrize(
    ("capability", "modes", "name", "problem"),
    [
        ("spreadsheets", {"read"}, None, "Unknown capability 'spreadsheets'"),
        ("team-graph", {"write"}, None, "must serve some of"),
        ("documents", set(), None, "must serve some of"),
        ("tracker", {"read"}, "Acme", "Adapter name 'Acme'"),
    ],
)
def test_provides_refuses_unknown_capabilities_modes_and_names(capability, modes, name, problem):
    with pytest.raises(ValueError, match=problem):
        Module("acme", "x").provides(capability, modes=modes, name=name)


class AcmeSettings(ModuleSettings):
    api_url: str = "https://api.acme.invalid"


def test_provides_reduces_static_urls_to_host_names():
    module = Module("acme", "x")
    module.provides("tracker", modes={"read"}, hosts=["https://API.acme.invalid/v3"])(object)
    let = next(let for let in module.lets if isinstance(let, ProviderLet))
    assert let.hosts == ("api.acme.invalid",)
    assert let.configured_hosts is None


def test_provides_takes_a_function_of_settings_for_hosts_configuration_decides():
    def hosts(settings: AcmeSettings) -> list[str]:
        return [settings.api_url]

    module = Module("acme", "x")
    module.provides("tracker", modes={"read"}, hosts=hosts)(object)
    let = next(let for let in module.lets if isinstance(let, ProviderLet))
    assert let.hosts == ()
    assert let.configured_hosts is hosts


@pytest.mark.parametrize(
    ("hosts", "problem"),
    [(["not a host"], "hosts must be host names"), (lambda: ["api.acme.invalid"], "takes no settings")],
)
def test_provides_refuses_invalid_hosts(hosts, problem):
    with pytest.raises(ValueError, match=problem):
        Module("acme", "x").provides("tracker", modes={"read"}, hosts=hosts)(object)


@pytest.mark.parametrize("schedule", ["\uff10 9 * * MON", "0 9 * *", "@reboot", "0 9 * * MON extra"])
def test_jobs_refuse_schedules_that_arent_five_ascii_cron_fields(schedule):
    with pytest.raises(ValueError, match="must be five cron fields"):
        JobLet(name="x", func=lambda: None, summary="s", schedule=schedule)


def test_operations_are_for_experts_unless_declared_everyday():
    module = Module("example", "x")

    @module.operation
    def deep() -> None:
        """For experts."""

    @module.operation(audience="everyday")
    def daily() -> None:
        """For everyone."""

    audiences = {let.name: let.audience for let in module.lets if isinstance(let, OperationLet)}
    assert audiences == {"deep": "expert", "daily": "everyday"}


def test_an_unknown_audience_is_refused():
    module = Module("example", "x")
    with pytest.raises(ValueError, match="audience 'everyone'"):

        @module.operation(audience="everyone")  # type: ignore[arg-type]
        def wrong() -> None:
            """Wrong."""


def test_a_module_may_name_what_it_was_called_before():
    assert Module("notes", "x", renamed_from="jottings").renamed_from == "jottings"
    assert Module("notes", "x").renamed_from is None


@pytest.mark.parametrize("old", ["Jottings", "hz", "setup", ""])
def test_a_renamed_module_needs_an_old_name_a_module_could_have_had(old):
    with pytest.raises(ValueError, match="can't be renamed from"):
        Module("notes", "x", renamed_from=old)


def test_operation_aliases_are_command_paths_normalised_to_single_spaces():
    module = Module("notes", "x")

    @module.operation(aliases=["jottings  run", "inbox"])
    def wake() -> None:
        """Wake."""

    (let,) = module.lets
    assert isinstance(let, OperationLet)
    assert let.aliases == ("jottings run", "inbox")


@pytest.mark.parametrize("alias", ["", "  ", "Jottings run", "jottings run_now"])
def test_operation_aliases_must_be_lowercase_words(alias):
    module = Module("notes", "x")
    with pytest.raises(ValueError, match="command paths of lowercase words"):

        @module.operation(aliases=[alias])
        def wake() -> None:
            """Wake."""


def test_a_module_has_an_offline_and_a_live_health_provider_and_no_second_of_either():
    module = Module("probe", "Checks things for tests.")

    @module.health
    def offline() -> list[Check]:
        return []

    @module.health(live=True)
    def live() -> list[Check]:
        return []

    lets = [let for let in module.lets if isinstance(let, HealthLet)]
    assert [(let.name, let.live) for let in lets] == [("probe", False), ("probe live", True)]
    with pytest.raises(ValueError, match="already has a HealthLet named 'probe live'"):
        module.health(live=True)(live)


def test_no_adapter_may_be_named_none():
    module = Module("none", "Would shadow the binding that binds nothing.")
    with pytest.raises(ValueError, match="reserved"):
        module.provides("documents", modes={"read"})(object)


def test_a_job_rejects_a_nonpositive_maximum_tick_interval():
    with pytest.raises(ValueError, match="maximum tick interval must be positive"):
        JobLet(name="scheduled", func=lambda: None, summary="x", schedule="*/5 * * * *", max_tick_interval=timedelta(0))
