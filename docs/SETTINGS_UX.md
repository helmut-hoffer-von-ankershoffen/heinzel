# Settings that need no explaining

The web app's Settings is built from each setting's JSON Schema: the title is the label, the description is the line
of help under it, and the type, `enum`, `format` and `x-` keys pick the control. A module or plugin that describes its
settings well gets a good control for free; one that doesn't gets a text field, and nobody wants to type into it.

These are the rules a reviewer applies to every setting. The mechanical ones are tests
(`tests/unit/settings_ux_test.py` for the schemas, the web app's settings tests for the controls).

## Principles

1. **Never make people type what can be chosen.** Known values are a choice: a `Literal` (an `enum`), an `x-options`
   source the app resolves (installed modules, adapters), a time zone picker, a toggle. A free-text field is only for
   values nobody can know in advance: a name, a handle, a search.
2. **Pick, don't parse.** Times of day are chosen in the viewer's own clock (9 AM in the US, 09:00 elsewhere), not
   typed as 9 or "09-18". Durations are a number and a unit ("36 hours"), not "36h". Amounts show their unit
   ("$0.25", "60 seconds", "30 days").
3. **Order means something, so let people order it.** In a list where the first entry is special (your own time zone
   first), the list can be reordered, the first is marked, and adding "my own" is one click.
4. **Group by what people think about, not by where the code lives.** Every setting lives in exactly one place:
   Preferences (appearance, World Clock, notifications), Customize (AI, connectors, plugins, skills), Organisation
   (you, team) and This computer (daemon, files, From Claude, updates). A module gets its own entry only where it adds a
   page of its own; settings of a plugin that connects a tool belong with that connector; a section is called by its
   title ("Notifications"), never by its TOML table (`[system.notify]`). Every entry has an icon of its own, and every
   module's page opens with the same header: its name and kinds on the left, where it comes from and its version on the
   right. A "show in …" setting is a row with the placement in words, a live preview of how it looks there and a switch.
5. **Show only what matters now.** Deprecated settings are never shown (doctor names the fix when one is still set);
   expert settings (`x-expert`) only in expert mode; a setting another control already edits (`x-hidden`) only there.
6. **Plain words.** A title is a short label in sentence case ("Log folder", not "Log Dir"); the description is one
   or two sentences without backticks, ADR numbers, TOML snippets or environment-variable syntax. Examples go into
   `examples`, where they become the field's placeholder, not into the prose.
7. **Say where a value comes from.** A value set by an environment variable says so and can't be changed here; a
   module's settings show which plugin brings them, its kind and its source, with a link.
8. **Secrets never sit in a text box.** A secret (`SecretStr`, `writeOnly`) shows whether it is set and how to set it
   (the keychain, or the environment variable), never its value.
9. **Paths can be opened.** A file or folder setting (`format: path`) has Show in Finder (or the desktop's file
   manager) next to it.
10. **Every change can be taken back.** Changes save at once, with an Undo for a few seconds and Reset to go back to
    the default, which is shown.
11. **Errors say how to fix them, where they happen.** A value that doesn't validate is refused under the setting,
    with what would be accepted.
12. **Search finds settings by what they are called and what they do**, not only by their key.

## The schema keys Settings reads

| Key | Where | What Settings does |
| --- | --- | --- |
| `title=` | `Field` | The label. Without it the field name is turned into one. |
| `description=` | `Field` | The help line; plain words (principle 6). |
| `examples=` | `Field` | The first example is the placeholder of an empty field. |
| `Literal[...]` / `enum` | type | A choice. |
| `x-labels` | `json_schema_extra` | What each choice is called: `{"DEBUG": "Everything, for debugging"}`. |
| `x-options` | `json_schema_extra` | Choices the app looks up: `{"source": "modules"}` (installed modules), `{"source": "adapters", "capability": ...}`. |
| `format: iana-timezone` | `json_schema_extra` or a type | A searchable time zone picker. |
| `format: path` | `Path` | A path with Show in Finder. |
| `format: duration` | `json_schema_extra` | A number and a unit, stored as `90m`, `36h` or `2d`. |
| `format: uri`, `format: email` | `json_schema_extra` | Checked as you type. |
| `writeOnly` | `SecretStr` | Set or not set, and how to set it; never the value. |
| `x-unit` | `json_schema_extra` | What a number counts: `seconds`, `minutes`, `hours`, `days`, `weeks`, `usd`, `per hour`; `hour-of-day` for hours of a day, shown in the viewer's 12- or 24-hour clock. |
| `x-expert` | `json_schema_extra` | Listed only in expert mode. |
| `x-hidden` | `json_schema_extra` | Never listed: another control edits it. |
| `deprecated` | `json_schema_extra` | Never listed. |
| `section_title` | ClassVar on the settings model | The section's heading. |

A list of objects (`list[Zone]`) becomes a table: one row per item with a control per property, buttons to move a row
up or down and to remove it, and Add.

## Walking a setting

Ask of each one what someone using it for the first time would say:

- Would I know what to put here without reading the code? If not: a choice, a picker, an example, or better words.
- Is there a value I would always want first? That is the default, shown, and Reset brings it back.
- Do I need to see this at all? If only to debug or for one rare setup: `x-expert`.
- Would a typo break something silently? Then the schema should refuse it, and the error should say what fits.
