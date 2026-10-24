# heinzel-clock

What time it is for everyone on the team — a first-party [Heinzelmännchen](../../README.md) plugin, and the smallest
complete example of one: [`src/heinzel_clock/__init__.py`](src/heinzel_clock/__init__.py) is the whole plugin.

```bash
hz system plugin add clock
hz clock setup                            # pick the team's time zones and the working hours in each
hz clock                                  # the team's local times; --json for agents
hz system config set clock.zones '[{ name = "Europe/Berlin", hours = [9, 18] }, "America/New_York"]'
```

Each zone is an IANA name, which gets working hours 9 to 18, or a table with its own `hours`. The same operation is
the MCP tool `clock` (`hz system serve mcp`) and `GET /clock` (`hz system serve api`); the zones are checked by
`hz system doctor`, and `hz setup` includes the clock's optional setup step.

The settings before per-zone hours (`zones` as names and one `working_hours` for all) are still read, with a
deprecation warning and a degraded doctor check; `hz clock setup` rewrites them in the new shape.
