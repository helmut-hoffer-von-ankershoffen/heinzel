---
title: Your first session
description: Find your way around Heinzelmännchen, inspect its operations, and choose your next useful step.
---

# A useful first session.

[Install the standard app](install.md), then run `hz ui` and open the local address it prints. Keep the terminal
open while you explore. There is no team repository or cloud service required just to inspect the workspace.

## Orient yourself

Start with **Today** and inspect the current state. Open settings to choose the theme and configure the modules
you want. The standard profile includes a world clock and system resource views. Available widgets and menu
entries follow enabled contributions; plugins own those features.

Heinzelmännchen communicate states such as working, waiting for you and blocked. Treat the accompanying words
as the explanation. Motion and playful decoration can be disabled in settings.

## Learn one capability from every angle

```sh
hz --help
hz system doctor
hz system plugin search --text clock --json
```

Operations have typed inputs and output. The same operation can be accessible from CLI, MCP and REST, subject to its privacy and head restrictions,
so learning it in the terminal also teaches you what an agent or application can call. Explore the
[generated CLI reference](../CLI_REFERENCE.md) and [module reference](../MODULES.md).

## Choose your next step

| Your goal | Start here |
| --- | --- |
| Connect a tool | [Plugin catalog](../CATALOG.md): inspect the owning source and setup requirements. |
| Coordinate a team | [Manager guide](manager.md) or [joining guide](joining.md). |
| Model repeatable work | [Your Heinzelmännchen](../HEINZELMAENNCHEN.md) and [playbooks](../PLAYBOOKS.md). |
| Build your own capability | [Create a plugin](../PLUGIN_AUTHORING.md): start with one operation and one test. |
| Understand the design | [Architecture tour](architecture.md) and [vision](../VISION.md). |

This release establishes the extensible foundation. Richer end-to-end team workflows remain work in progress;
use the owning module's documentation and health checks to assess each integration you enable.
