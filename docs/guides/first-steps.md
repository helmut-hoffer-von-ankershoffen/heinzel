---
title: Your first session
description: Find your way around Heinzelmännchen and choose your next useful step.
---

# A useful first session.

Already have the app open? Start here. If not, follow the [installation guide](install.md) first.

## Find your way around

**Today** is your starting point. Use the navigation to move between screens. The chat beside every screen is
always with one of your Heinzels: at first start you name your first one, and its name in the chat's header switches
to another. The account menu at the bottom of the navigation brings together **Settings**, **Profile**,
**Set up hz**, **Check this machine** and **Get help**.

## Make it yours

1. Open the account menu and choose **Set up hz**. Add your name, role and time zone; you can leave the team
   repository empty to start alone.
2. Pick a theme. You can change it later in **Settings**.
3. If you want to use a heinzel to draft work, choose AI access in **Settings → General**. Connectors have their
   own setup and may need you to sign in.
4. Choose how you want to receive notifications, and use the test buttons in setup to check them.

To measure local chat latency without an AI service, select **Dummy (always answers 42)** in AI access, or run
`hz ai access use dummy`. It answers every message with `42` and does not use tools. Select your usual AI access again
when you finish testing. Open **Chat diagnostics** in the chat header to inspect preparation, provider requests,
internal tool calls, MCP discovery, and MCP tool calls. Tool timings include failed calls and may overlap when
calls run concurrently. Configured servers are separate from tools offered to the model; cached descriptor counts
are a passive snapshot, not evidence that a tool was offered or called. Diagnostics do not connect to servers.
The sidebar keeps at most 20 turns in memory and clears when browser authority changes.

You can return to setup from the account menu. You can also use **Check this machine** and **Get help** if you get
stuck.

## Choose your next step

| Your goal | Start here |
| --- | --- |
| Explore the screens | [Product tour](tour.md). |
| Connect a tool | [Plugin catalog](../CATALOG.md): inspect the owning source and setup requirements. |
| Coordinate a team | [Manager guide](manager.md) or [joining guide](joining.md). |
| Model repeatable work | [Your Heinzelmännchen](../HEINZELMAENNCHEN.md) and [playbooks](../PLAYBOOKS.md). |
| Build your own capability | [Create a plugin](../PLUGIN_AUTHORING.md): start with one operation and one test. |
| Use the command line | [CLI reference](../CLI_REFERENCE.md) and [modules](../MODULES.md). |
