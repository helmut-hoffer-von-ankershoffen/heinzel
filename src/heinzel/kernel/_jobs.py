"""Jobs compiled from data rather than declared in code: workflow contracts (ADR 0021)."""

from dataclasses import dataclass

from heinzel.kernel._lets import JobLet


@dataclass(frozen=True, kw_only=True, slots=True)
class CompiledJobs:
    """Jobs compiled from data the module reads, rather than declared in code, e.g. workflow contracts (ADR 0021).

    One module provides it, as a service whose factory compiles them (`module.service(CompiledJobs, compile)`): in
    the core, `workflow`, the single compiler of every contract source. The daemon runs them beside the declared
    `JobLet`s as `<providing module>:<job>`, each job acting as a `Requester` of kind `job` of that module, which
    `Kernel.handle_for` serves with read handles only.
    """

    jobs: tuple[JobLet, ...] = ()
