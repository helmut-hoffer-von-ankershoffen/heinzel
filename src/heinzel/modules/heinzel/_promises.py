"""The `TeamPromises` workflow provides: what each valid team contract promises the team (ADR 0054).

The `graph` module holds the team to these promises; it reads them through this kernel type rather than the contracts,
so it never parses a contract and never sees a body.
"""

from typing import override

from heinzel.kernel import TeamPromise, TeamPromises
from heinzel.modules.heinzel._graph import asset
from heinzel.modules.heinzel._service import HeinzelService


class ContractPromises(TeamPromises):
    """Team contracts read afresh on every call, so a pull merged into the checkout counts at once."""

    def __init__(self, service: HeinzelService) -> None:
        """Read the contracts through `service`."""
        self._service = service

    @override
    def promises(self) -> tuple[TeamPromise, ...]:
        """Every valid team contract's promise; a contract with errors promises nothing until it is fixed."""
        found: list[TeamPromise] = []
        for contract in self._service.contracts():
            if contract.namespace != "team" or not contract.valid or contract.frontmatter is None:
                continue
            frontmatter = contract.frontmatter
            hz = frontmatter.hz
            (output, *_) = hz.outputs
            reads = {spec.read for spec in hz.inputs.values() if (up := spec.upstream) is not None and up[0] == "team"}
            found.append(
                TeamPromise(
                    contract=contract.qualified,
                    title=hz.title,
                    version=frontmatter.version or contract.sha256,
                    schedule=hz.schedule,
                    writes=asset(contract, output),
                    reads=tuple(sorted(reads)),
                    publishes=tuple(sorted({p.action for p in hz.publishes})),
                )
            )
        return tuple(sorted(found, key=lambda p: p.contract))
