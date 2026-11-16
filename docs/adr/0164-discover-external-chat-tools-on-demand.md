---
status: proposed
date: 2026-11-15
implementation: built
amends: 0162
amends-0162: a turn discovers enabled external MCP tools on demand rather than loading every schema before its first model request
related: 0146, 0159
---

# 0164: Discover external chat tools on demand

## Context and Problem Statement

A turn currently opens each enabled MCP server serially to obtain its tool schemas before making its first model
request. A slow or unavailable server can delay a text-only turn for minutes, including on every turn after failure.
The maintainer wants fast turns and an explicitly selectable local model to measure the whole path.

## Considered Options

- Wait for every server before the model starts: full schemas are immediately visible, but one server blocks all turns.
- Load enabled servers on demand through an explicit gateway: tools remain discoverable without blocking text-only turns.
- Hide servers that have not started: rejected, because an enabled tool must not disappear silently.

## Decision Outcome

A turn advertises an external-tool gateway immediately. Its directory lists enabled, unambiguous server namespaces
without opening them. The model can list one server's tools, read an individual tool's schema in chunks and call it.
Server discovery and tool calls are on demand; a text-only answer does not start external servers.

A tool-using turn can still take as long as the selected server and the model require. The directory and schemas are
untrusted data.

The gateway rechecks the enabled server and exact tool name when used. A call uses the existing `FoundTool` policy:
read-only tools run, and every other tool is proposed for the person's confirmation. Name collisions are refused.
Disabled, missing or broken servers and tools are explicitly reported as unavailable. Server lists and tool lists
are paginated; schema chunks carry the next offset.

No gateway is offered for another owner's turn.

This changes the model-visible contract: the model now discovers external tools through a gateway instead of receiving
all of their schemas at once. It may need extra model requests when it actually uses an external tool. Legacy server
providers can still supply their existing listings, though they cannot promise cheap directory enumeration.

The **Dummy (always answers 42)** model can be selected for local timing without sending prompts to an AI service.
It is not a substitute for measuring a real provider, a slow tool call or a browser's rendering time.

### Consequences

- Text-only turns no longer wait for external MCP startup. A deliberately slow or broken enabled server does not
  prevent the dummy model from answering.
- Discovering an external server or calling its tools may wait for that server; this work is not hidden or claimed
  to complete within one second.
- Models need the gateway instructions and may consume extra requests to discover a tool. Existing owner and write
  authorization checks remain in code, not in those instructions.

### Confirmation

Focused tests exercise cold and warm text-only turns with slow and broken enabled servers, selected-server discovery,
schema chunks, configuration changes, and the existing read and write-confirmation policy. They measure local
authenticated HTTP handling separately from external provider and browser latency.

The scale fixture includes 64 configured slow or failing servers and 163 internal tools (35 built-in and 128
synthetic read operations). On the development machine, a focused run measured the following in-process HTTP
request-to-answer times with the local dummy provider:

| Configured servers | Internal tools | First turn | Next turn | Selected server discovery |
| --- | ---: | ---: | ---: | ---: |
| None | 35 | 61 ms | 55 ms | Not requested |
| One slow | 35 | 56 ms | 56 ms | 1135 ms |
| One failing | 35 | 58 ms | 56 ms | 2848 ms |
| 64 slow | 163 | 88 ms | 86 ms | 1138 ms |
| 64 failing | 163 | 89 ms | 86 ms | 2844 ms |

These first turns have cold server listings, but the Python process and SDK were already loaded by unit tests.
A separate fresh integration-test process measured 958 ms for its first answer with 35 internal tools and no
servers, followed by 57 ms warm. This includes first-turn SDK imports, not kernel initialization or process startup.
It is an observation, not a timing threshold or a guarantee on another machine.

The slow fixture adds 1.1 seconds to connection startup; the failing fixture uses a 750 ms request timeout and
also incurs client shutdown. No server process starts during either text-only turn. Explicit discovery starts
only the requested server; the repeated listing or failure uses its cache (1–4 ms in this run). Tests retain
configuration-revocation checks and verify that every internal fixture tool remains available. A real model's
generation, network transport, browser rendering and tool-using turns remain outside these measurements.
