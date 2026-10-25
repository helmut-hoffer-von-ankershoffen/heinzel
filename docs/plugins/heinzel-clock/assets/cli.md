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

 v0.7.5                                                                  🐻 built with AI in Berlin
```

```console
$ hz clock
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Zone                  ┃ Time      ┃ Working hours ┃ Working ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Europe/Berlin (yours) │ Sun 18:06 │ 09-18         │ no      │
│ America/New_York      │ Sun 13:06 │ 09-18         │ no      │
│ America/Los_Angeles   │ Sun 10:06 │ 09-18         │ no      │
└───────────────────────┴───────────┴───────────────┴─────────┘
```

```console
$ hz clock --json
[
  {
    "zone": "Europe/Berlin",
    "time": "Sun 18:06",
    "hours": [
      9,
      18
    ],
    "working": false,
    "primary": true
  },
  {
    "zone": "America/New_York",
    "time": "Sun 13:06",
    "hours": [
      9,
      18
    ],
    "working": false,
    "primary": false
  },
  {
    "zone": "America/Los_Angeles",
    "time": "Sun 10:06",
    "hours": [
      9,
      18
    ],
    "working": false,
    "primary": false
  }
]
```
