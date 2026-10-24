---
name: hz-uninstall
description: Uninstall Heinzelmännchen from this machine - its agent skills, the hz CLI and its plugins, and optionally its configuration and local data. Use when the user asks to uninstall, remove or clean up Heinzelmännchen, or types /hz-uninstall.
---

# hz-uninstall

1. Confirm with the user what to remove. By default the CLI, its plugins and Heinzelmännchen's agent skills go; the
   configuration and local data stay. Only with their explicit go-ahead, add `--purge` to also delete the user
   configuration file and Heinzelmännchen's data and cache directories (`hz system config show` lists them — show the user).
2. Run the script that ships with this skill:

   ```bash
   bash "<this skill's directory>/scripts/uninstall.sh"          # or: ... uninstall.sh --purge
   ```

3. Verify: `command -v hz` finds nothing. A project's `hz.toml` files are never touched; mention them if the user
   wants a clean slate.
