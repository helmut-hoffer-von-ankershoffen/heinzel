```console
$ hz google --help

 Usage: hz google [OPTIONS] COMMAND [ARGS]...

 Google Docs, Sheets, Drive, Calendar and Gmail: the documents, calendar and mail capabilities.

╭─ Options ────────────────────────────────────────────────────────────────────────────────────────╮
│ --help  -h        Show this message and exit.                                                    │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────────────────────────╮
│ files  List the Google Docs and Sheets you can open that changed recently, newest first.         │
│ info   Show this plugin's version, the Heinzelmännchen it runs on and the gws, scopes and APIs   │
│        it uses.                                                                                  │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯

 v0.7.5                                                                  🐻 built with AI in Berlin
```

```console
$ hz google files
Error: gws is not signed in to Google, or its sign-in expired (Access denied. No credentials provided. Run `gws auth login` or set GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE to an OAuth credentials JSON file.). Sign in: gws auth login --scopes https://www.googleapis.com/auth/documents.readonly,https://www.googleapis.com/auth/spreadsheets.readonly,https://www.googleapis.com/auth/drive.metadata.readonly,https://www.googleapis.com/auth/calendar.readonly (add ,https://www.googleapis.com/auth/gmail.readonly if you read mail from Google)
```

```console
$ hz google files --json
Error: gws is not signed in to Google, or its sign-in expired (Access denied. No credentials provided. Run `gws auth login` or set GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE to an OAuth credentials JSON file.). Sign in: gws auth login --scopes https://www.googleapis.com/auth/documents.readonly,https://www.googleapis.com/auth/spreadsheets.readonly,https://www.googleapis.com/auth/drive.metadata.readonly,https://www.googleapis.com/auth/calendar.readonly (add ,https://www.googleapis.com/auth/gmail.readonly if you read mail from Google)
```
