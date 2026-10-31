---
title: Your first session
description: Find your way around Heinzel, inspect its operations, and choose your next useful step.
---

# A useful first session.

[Install the standard app](install.md), run `hz ui` and open the address it prints. Keep the terminal open. You
need no team repository or cloud service.

## Orient yourself

Open **Today**. In settings, pick a theme and enable modules. The standard app has a world clock and resource
views; plugins own every other widget and menu entry.

Helpers show a state: working, waiting for you, blocked. The words explain it. Turn off motion and decoration in
settings.

## Learn one capability from every angle

```sh
hz --help
hz system doctor
hz system plugin search --text clock --json
```

An operation has typed inputs and output, and usually works in the CLI, MCP and REST alike. What you learn in the
terminal is what an agent can call. See the [CLI reference](../CLI_REFERENCE.md) and [modules](../MODULES.md).

## Choose your next step

| Your goal | Start here |
| --- | --- |
| Connect a tool | [Plugin catalog](../CATALOG.md): inspect the owning source and setup requirements. |
| Coordinate a team | [Manager guide](manager.md) or [joining guide](joining.md). |
| Model repeatable work | [Your Heinzelmännchen](../HEINZELMAENNCHEN.md) and [playbooks](../PLAYBOOKS.md). |
| Build your own capability | [Create a plugin](../PLUGIN_AUTHORING.md): start with one operation and one test. |
| Understand the design | [Architecture tour](architecture.md) and [vision](../VISION.md). |

This release is the foundation. Team workflows that run on a schedule without you are not built yet; check each integration's docs and
health before you rely on it.
