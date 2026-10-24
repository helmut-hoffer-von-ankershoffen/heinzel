# heinzel-clock

What time it is for everyone on the team — a first-party [Heinzelmännchen](../../README.md) plugin, and the smallest
complete example of one: [`src/heinzel_clock/__init__.py`](src/heinzel_clock/__init__.py) is the whole plugin.

```bash
hz system plugin add clock
hz clock                                  # the team's local times; --json for agents
hz system config set clock.zones '["Europe/Berlin", "America/New_York"]'
```

The same operation is the MCP tool `clock` (`hz system serve mcp`) and `GET /clock` (`hz system serve api`); the
zones are checked by `hz system doctor`.
