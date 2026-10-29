"""Implementation-free service contracts, recognizable even when no provider is installed."""


class ServiceContract:
    """Base for a required injected interface declared in the SDK or a neutral contract package.

    A provider registers an implementation with ``module.service(Contract, Implementation)``. Consumers annotate
    parameters with the contract. Missing providers make only dependent operations unavailable, including through
    service factories; the interface never becomes a CLI argument, MCP input or API request parameter.
    """
