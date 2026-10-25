```console
$ hz clock --help

 Usage: hz clock [OPTIONS]

 Show the time in each of the team's time zones.

╭─ Options ────────────────────────────────────────────────────────────────────────────────────────╮
│ --at              ISO-8601     Moment to show instead of now (ISO 8601).                         │
│ --format          <text|json>  Output format; defaults to the output_format setting              │
│                                (HZ_OUTPUT_FORMAT), which is text.                                │
│ --json                         Shorthand for --format json, for scripts and agents.              │
│ --help    -h                   Show this message and exit.                                       │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯

 🧝 Heinzelmännchen hz 0.4.0 · open-source helpers that keep product teams running
```

```console
$ hz clock
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Zone                  ┃ Time      ┃ Working hours ┃ Working ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Europe/Berlin (yours) │ Sun 00:58 │ 09-18         │ no      │
│ America/New_York      │ Sat 18:58 │ 09-18         │ no      │
│ America/Los_Angeles   │ Sat 15:58 │ 09-18         │ no      │
└───────────────────────┴───────────┴───────────────┴─────────┘
```

```console
$ hz clock --json
[
  {
    "zone": "Europe/Berlin",
    "time": "Sun 00:58",
    "hours": [
      9,
      18
    ],
    "working": false,
    "primary": true
  },
  {
    "zone": "America/New_York",
    "time": "Sat 18:58",
    "hours": [
      9,
      18
    ],
    "working": false,
    "primary": false
  },
  {
    "zone": "America/Los_Angeles",
    "time": "Sat 15:58",
    "hours": [
      9,
      18
    ],
    "working": false,
    "primary": false
  }
]
```
