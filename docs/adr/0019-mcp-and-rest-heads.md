---
status: accepted
date: 2026-10-23
amended-by: 0039
---

# 0019: MCP and REST heads derive everything from operations

*Since 0051 the module is `heinzel`: read `hz workflow` as `hz heinzel` and `[workflow]` as `[heinzel]`.*

## Context and Problem Statement

Coding agents talk MCP; other systems talk HTTP. ADR 0003 promised that operations written once would appear in
every head. Plugins should get both without a line of head-specific code.

## Considered Options

- **MCP and REST heads derived from operations through one shared exposure rule** (chosen)

No alternatives were recorded when this decision was made.

## Decision Outcome

Chosen option: **MCP and REST heads derive everything from operations**, because every module, built-in or plugin,
then serves both without extra code.

- One exposure rule for all heads (`heads/_exposure.py`): an operation's path (`("system", "doctor")`), its public
  parameters (injected ones removed) and its return type; modules collapse like in the CLI; an operation's
  `heads=` (e.g. `{"cli"}` for interactive ones) restricts where it appears.
- **MCP head** (official `mcp` SDK, `MCPServer`): a tool per operation, named `system_doctor`; input schema from the
  `Annotated[T, Field(...)]` parameters, output schema from the return model, `read_only` mapped to MCP's
  read-only/destructive hints, `HeinzelError` to tool errors. `hz system serve mcp` (stdio, or streamable HTTP).
- **REST head** (FastAPI): an endpoint per operation at `/system/doctor`, `GET` when read-only and `POST` otherwise,
  query parameters described by their `Field`, OpenAPI at `/docs`. `HeinzelError` and invalid arguments → `400
  {"error": ...}`; an operation whose CLI exit code would be non-zero → `503`, so `/system/doctor` is a health probe.
  `hz system serve api`.
- Arguments are validated by the kernel (`Kernel.invoke`) in every head, so behaviour is identical.
- The heads are imported only when served, so `hz` stays fast.

### Consequences

- Good: every module, built-in or plugin, is an MCP server and a REST API without extra code.
- Neutral: head-specific lets (MCP resources and prompts, REST webhooks) remain possible later; operations stay the
  default.

### Confirmation

`tests/integration/heads_test.py` (`test_mcp_tools`, `test_rest_endpoints`), `tests/unit/heads/exposure_test.py`
(`test_paths_names_and_public_parameters`, `test_heads_restrict_exposure`),
`tests/unit/kernel/kernel_test.py::test_invoke_validates_arguments_against_their_annotations`; import-linter's
"Heads only know what the kernel discovered".

## More Information

- **The REST head answers only local, same-origin requests.** It has
  no authentication, and a web page in the user's browser can reach `127.0.0.1` too, directly or through DNS
  rebinding. So it refuses with `403` and a message saying why any request whose Host isn't `localhost`,
  `127.0.0.1` or `[::1]` on the port it listens on, unless the user lists the name in `[hz] api_allowed_hosts` (user
  scope only, for serving on another interface; on port 80 or 443 a Host without a port counts), and any request
  carrying an Origin that isn't its own, or, without an Origin, `Sec-Fetch-Site: cross-site` or `same-site`. Operations
  that spend money or act unattended are not REST endpoints at all: `hz workflow run` and `hz system daemon tick`
  declare `heads={"cli", "mcp"}` (launchd and systemd run the CLI). Tests: `tests/integration/rest_security_test.py`.
