# CLI reference

🧝 Heinzelmännchen: open-source helpers that keep product teams running. Every operation accepts --json.

**Usage**:

```console
$ hz [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-V, --version`: Show the version and repository, Python, platform, installation and plugins, then exit.
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `atlassian`: Read Jira issues and Confluence pages...
* `clock`: Show the time in each of the team&#x27;s time...
* `github`: Read pull requests and issues from GitHub:...
* `org`: Your profile, and the team, org and...
* `setup`: Set up every module that has a setup step,...
* `system`: Inspect, configure, diagnose and update...
* `workflow`: Workflow contracts: Markdown files an...
* `zoom`: Read meetings from Zoom&#x27;s meeting-summary...

## `hz atlassian`

Read Jira issues and Confluence pages through acli: the tracker and documents capabilities.

**Usage**:

```console
$ hz atlassian [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `info`: Show this plugin&#x27;s version, the Atlassian...

### `hz atlassian info`

Show this plugin&#x27;s version, the Atlassian site and the acli commands it runs.

**Usage**:

```console
$ hz atlassian info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz clock`

Show the time in each of the team&#x27;s time zones.

**Usage**:

```console
$ hz clock [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--at ISO-8601`: Moment to show instead of now (ISO 8601).
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

**Commands**:

* `setup`: Pick the team&#x27;s time zones and the working...

### `hz clock setup`

Pick the team&#x27;s time zones and the working hours in each.

**Usage**:

```console
$ hz clock setup [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `hz github`

Read pull requests and issues from GitHub: the code-host and tracker capabilities.

**Usage**:

```console
$ hz github [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `info`: Show this plugin&#x27;s version, the...

### `hz github info`

Show this plugin&#x27;s version, the Heinzelmännchen it runs on and the GitHub host it reads.

**Usage**:

```console
$ hz github info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz org`

Your profile, and the team, org and company you work with.

**Usage**:

```console
$ hz org [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `setup`: Tell hz who you are; what your gh and gws...
* `show`: Show your profile: who you are, your role,...

### `hz org setup`

Tell hz who you are; what your gh and gws logins and this machine say is found, the rest is asked once.

**Usage**:

```console
$ hz org setup [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `hz org show`

Show your profile: who you are, your role, time zone and working hours, and what is still missing.

**Usage**:

```console
$ hz org show [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz setup`

Set up every module that has a setup step, wizard style; optional steps can be skipped.

**Usage**:

```console
$ hz setup [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `hz system`

Inspect, configure, diagnose and update Heinzelmännchen itself.

**Usage**:

```console
$ hz system [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `agent`: Install the Heinzelmännchen skills for...
* `capability`: Show which adapters serve which...
* `changelog`: Show the changelog of the installed...
* `config`: Show, get, set and initialize configuration.
* `credential`: Store and check the credentials modules...
* `daemon`: Run modules&#x27; jobs in the background,...
* `doctor`: Run every module&#x27;s health checks; exits...
* `info`: Show versions, interpreter, settings,...
* `modules`: List every module and the lets it advertises.
* `plugin`: Find, add, remove, list and scaffold plugins.
* `release`: Ask where Heinzelmännchen was installed...
* `serve`: Serve every operation to coding agents...
* `update`: Update Heinzelmännchen, keeping installed...

### `hz system agent`

Install the Heinzelmännchen skills for coding agents.

**Usage**:

```console
$ hz system agent [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `install`: Copy Heinzelmännchen&#x27;s skills into each...
* `skills`: List the Agent Skills that ship with...
* `uninstall`: Remove Heinzelmännchen&#x27;s skills from each...

#### `hz system agent install`

Copy Heinzelmännchen&#x27;s skills into each configured agent skill directory.

**Usage**:

```console
$ hz system agent install [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system agent skills`

List the Agent Skills that ship with Heinzelmännchen.

**Usage**:

```console
$ hz system agent skills [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system agent uninstall`

Remove Heinzelmännchen&#x27;s skills from each configured agent skill directory.

**Usage**:

```console
$ hz system agent uninstall [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system capability`

Show which adapters serve which capabilities, and who reads and writes what.

**Usage**:

```console
$ hz system capability [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List capabilities, their bindings and...

#### `hz system capability list`

List capabilities, their bindings and adapters, and every requirement with its adapter and hosts.

**Usage**:

```console
$ hz system capability list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system changelog`

Show the changelog of the installed version, as Markdown.

**Usage**:

```console
$ hz system changelog [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system config`

Show, get, set and initialize configuration.

**Usage**:

```console
$ hz system config [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `get`: Show one setting in effect, with its...
* `init`: Write a configuration file with every...
* `set`: Change a setting in the user or project...
* `show`: Show the configuration files...
* `unset`: Remove a setting from the user or project...

#### `hz system config get`

Show one setting in effect, with its environment variable.

**Usage**:

```console
$ hz system config get [OPTIONS] {key}
```

**Arguments**:

* `key`: Setting as section.key, e.g. hz.log_level or example.greeting.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config init`

Write a configuration file with every setting commented out, ready to edit.

**Usage**:

```console
$ hz system config init [OPTIONS]
```

**Options**:

* `--path <path>`: Where to write; defaults to the user configuration file.
* `--force / --no-force`: Overwrite an existing file.  [default: no-force]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config set`

Change a setting in the user or project configuration file, after validating it.

**Usage**:

```console
$ hz system config set [OPTIONS] {key} {value}
```

**Arguments**:

* `key`: Setting as section.key, e.g. hz.log_level or example.greeting.  [required]
* `value`: New value; TOML syntax for numbers, booleans and lists.  [required]

**Options**:

* `--scope <user|project>`: Which file to change: user, or the project&#x27;s hz.toml.  [default: user]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config show`

Show the configuration files Heinzelmännchen reads and every setting in effect.

**Usage**:

```console
$ hz system config show [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config unset`

Remove a setting from the user or project configuration file, so its default applies again.

**Usage**:

```console
$ hz system config unset [OPTIONS] {key}
```

**Arguments**:

* `key`: Setting as section.key, e.g. hz.log_level or example.greeting.  [required]

**Options**:

* `--scope <user|project>`: Which file to change: user, or the project&#x27;s hz.toml.  [default: user]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system credential`

Store and check the credentials modules need, in the OS keychain.

**Usage**:

```console
$ hz system credential [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List the credentials modules declare and...
* `remove`: Remove a credential&#x27;s value from the...
* `set`: Store a credential, read from standard...

#### `hz system credential list`

List the credentials modules declare and whether each is stored, never their values.

**Usage**:

```console
$ hz system credential list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system credential remove`

Remove a credential&#x27;s value from the keychain.

**Usage**:

```console
$ hz system credential remove [OPTIONS] {name}
```

**Arguments**:

* `name`: Name of the credential, as `hz system credential list` shows it.  [required]

**Options**:

* `--service <str>`: Service it is kept under; needed only when several services declare the name.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system credential set`

Store a credential, read from standard input or a hidden prompt, never from the command line.

**Usage**:

```console
$ hz system credential set [OPTIONS] {name}
```

**Arguments**:

* `name`: Name of the credential, as `hz system credential list` shows it.  [required]

**Options**:

* `--service <str>`: Service it is kept under; needed only when several services declare the name.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system daemon`

Run modules&#x27; jobs in the background, supervised by launchd or systemd.

**Usage**:

```console
$ hz system daemon [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `install`: Install and load the launchd agent or...
* `jobs`: List every job with its schedule, next...
* `logs`: Show the latest lines of the daemon&#x27;s log.
* `pause`: Pause the daemon: ticks run no job until...
* `resume`: Resume a paused daemon.
* `status`: Show whether the daemon is installed,...
* `tick`: Run the jobs that are due and write a...
* `uninstall`: Unload and remove the daemon&#x27;s unit;...

#### `hz system daemon install`

Install and load the launchd agent or systemd timer that ticks every few minutes.

**Usage**:

```console
$ hz system daemon install [OPTIONS]
```

**Options**:

* `--interval <str>`: Minutes between ticks, dividing an hour: 1m … 30m, or 1h.  [default: 5m]
* `--dry-run / --no-dry-run`: Only show the unit that would be written.  [default: no-dry-run]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon jobs`

List every job with its schedule, next slot and last run.

**Usage**:

```console
$ hz system daemon jobs [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon logs`

Show the latest lines of the daemon&#x27;s log.

**Usage**:

```console
$ hz system daemon logs [OPTIONS]
```

**Options**:

* `--lines <int>`: How many of the latest lines to show.  [default: 50]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon pause`

Pause the daemon: ticks run no job until resumed.

**Usage**:

```console
$ hz system daemon pause [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon resume`

Resume a paused daemon.

**Usage**:

```console
$ hz system daemon resume [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon status`

Show whether the daemon is installed, loaded and paused, its last tick and failing jobs.

**Usage**:

```console
$ hz system daemon status [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon tick`

Run the jobs that are due and write a receipt for each; exits with 1 if any job failed.

**Usage**:

```console
$ hz system daemon tick [OPTIONS]
```

**Options**:

* `--dry-run / --no-dry-run`: Only show which jobs are due; run nothing.  [default: no-dry-run]
* `--job <str>`: Run this job now, as `&lt;module&gt;:&lt;job&gt;` from `hz system daemon jobs`.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon uninstall`

Unload and remove the daemon&#x27;s unit; receipts and logs stay.

**Usage**:

```console
$ hz system daemon uninstall [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system doctor`

Run every module&#x27;s health checks; exits with 1 if anything is down.

**Usage**:

```console
$ hz system doctor [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system info`

Show versions, interpreter, settings, modules and what modules report.

**Usage**:

```console
$ hz system info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system modules`

List every module and the lets it advertises.

**Usage**:

```console
$ hz system modules [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system plugin`

Find, add, remove, list and scaffold plugins.

**Usage**:

```console
$ hz system plugin [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: Add a plugin; adding one again replaces...
* `list`: List installed plugins and the modules...
* `new`: Scaffold a plugin repository from the...
* `remove`: Remove a plugin.
* `search`: Find plugins to install: first-party ones,...

#### `hz system plugin add`

Add a plugin; adding one again replaces the installed version.

**Usage**:

```console
$ hz system plugin add [OPTIONS] {spec}
```

**Arguments**:

* `spec`: A first-party plugin by name (`clock`), or anything uv installs: a git URL (git+https://github.com/org/heinzel-x@v1.0.0), a local path or a package name.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin list`

List installed plugins and the modules they bring.

**Usage**:

```console
$ hz system plugin list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin new`

Scaffold a plugin repository from the Heinzelmännchen template, asking for answers not given.

**Usage**:

```console
$ hz system plugin new [OPTIONS] {destination}
```

**Arguments**:

* `destination`: Directory for the new plugin repository.  [required]

**Options**:

* `--data <str>`: Answer a template question as key=value, e.g. plugin_name=crm; repeat for each.
* `--template <str>`: Copier template: a git URL, gh:owner/repo or a local path.  [default: gh:helmut-hoffer-von-ankershoffen/heinzel]
* `--ref <str>`: Template version; defaults to this Heinzelmännchen&#x27;s release tag.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin remove`

Remove a plugin.

**Usage**:

```console
$ hz system plugin remove [OPTIONS] {distribution}
```

**Arguments**:

* `distribution`: Distribution name, as `hz system plugin list` shows it.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin search`

Find plugins to install: first-party ones, and community ones by querying the GitHub API.

**Usage**:

```console
$ hz system plugin search [OPTIONS]
```

**Options**:

* `--text <str>`: Words that name or describe the plugin, e.g. `calendar`; sent to GitHub&#x27;s search. All plugins if omitted.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system release`

Ask where Heinzelmännchen was installed from for its latest release, and whether to update.

**Usage**:

```console
$ hz system release [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system serve`

Serve every operation to coding agents (MCP) or over HTTP (REST), or run the daemon in the foreground.

**Usage**:

```console
$ hz system serve [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `api`: Serve every operation as a REST endpoint;...
* `daemon`: Run the daemon&#x27;s tick in the foreground...
* `mcp`: Serve every operation as an MCP tool, e.g.

#### `hz system serve api`

Serve every operation as a REST endpoint; OpenAPI docs at /docs.

**Usage**:

```console
$ hz system serve api [OPTIONS]
```

**Options**:

* `--host <str>`: Interface to listen on.  [default: 127.0.0.1]
* `--port <int>`: Port to listen on.  [default: 8000]
* `--help`: Show this message and exit.

#### `hz system serve daemon`

Run the daemon&#x27;s tick in the foreground every interval, printing each result as a JSON line.

**Usage**:

```console
$ hz system serve daemon [OPTIONS]
```

**Options**:

* `--interval DURATION`: Time between ticks, e.g. 30s, 5m or 1h.  [default: 5m]
* `--help`: Show this message and exit.

#### `hz system serve mcp`

Serve every operation as an MCP tool, e.g. `claude mcp add hz -- hz system serve mcp`.

**Usage**:

```console
$ hz system serve mcp [OPTIONS]
```

**Options**:

* `--transport <stdio|streamable-http>`: stdio for agents that start hz; streamable-http to share.  [default: stdio]
* `--host <str>`: Interface for streamable-http.  [default: 127.0.0.1]
* `--port <int>`: Port for streamable-http.  [default: 8765]
* `--help`: Show this message and exit.

### `hz system update`

Update Heinzelmännchen, keeping installed plugins, and show the version now installed.

**Usage**:

```console
$ hz system update [OPTIONS]
```

**Options**:

* `--source <str>`: Install from here instead, e.g. git+https://github.com/org/heinzel@v1.2.0 or a wheel.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz workflow`

Workflow contracts: Markdown files an agent drafts from, by hand or on your daemon&#x27;s schedule (`hz workflow new` starts one).

**Usage**:

```console
$ hz workflow [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `check`: Check contracts and list every problem...
* `discover`: Find candidates for each playbook&#x27;s asset...
* `drafts`: List the drafts runs saved locally, newest...
* `list`: List the contracts in your and your team&#x27;s...
* `new`: Write a starter contract, or a playbook&#x27;s...
* `playbooks`: List the playbooks plugins contribute:...
* `prune`: Delete drafts and records older than...
* `run`: Render a contract now: fetch its inputs,...
* `sent`: Record how much of a draft you changed...
* `show`: Show a contract as the daemon runs it:...

### `hz workflow check`

Check contracts and list every problem with file and line; exits with 1 on errors, so CI can gate.

**Usage**:

```console
$ hz workflow check [OPTIONS]
```

**Options**:

* `--path <path>`: A contract file, or a directory searched for *.prose.md; every contract found if none.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow discover`

Find candidates for each playbook&#x27;s asset types through the capabilities you have bound; reads only.

**Usage**:

```console
$ hz workflow discover [OPTIONS]
```

**Options**:

* `--playbook <str>`: Only this module&#x27;s playbook; every playbook&#x27;s if omitted.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow drafts`

List the drafts runs saved locally, newest first, with their paths and records, or show the newest.

**Usage**:

```console
$ hz workflow drafts [OPTIONS]
```

**Options**:

* `--name <str>`: Only this contract&#x27;s drafts; every contract&#x27;s if omitted.
* `--latest / --no-latest`: Print the newest draft instead of the list.  [default: no-latest]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow list`

List the contracts in your and your team&#x27;s workflows directories, with their schedule and problems.

**Usage**:

```console
$ hz workflow list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow new`

Write a starter contract, or a playbook&#x27;s template (--template), into your workflows directory.

**Usage**:

```console
$ hz workflow new [OPTIONS] {name}
```

**Arguments**:

* `name`: The contract&#x27;s name: lowercase words joined by dashes, e.g. weekly-digest.  [required]

**Options**:

* `--team / --no-team`: Write it into the team repository&#x27;s workflows/ instead of your own directory.  [default: no-team]
* `--template <str>`: Start from a playbook&#x27;s template contract, `&lt;module&gt;:&lt;name&gt;` (`hz workflow playbooks` lists them), instead of the blank starter.
* `--asset <str>`: Fill a template&#x27;s asset placeholder as type=reference, e.g. weekly-report=doc-123 (`hz workflow discover` finds candidates); repeat for each.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow playbooks`

List the playbooks plugins contribute: asset types, template contracts and recommended settings.

**Usage**:

```console
$ hz workflow playbooks [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow prune`

Delete drafts and records older than ` retention_days` (default 90); receipts are kept.

**Usage**:

```console
$ hz workflow prune [OPTIONS]
```

**Options**:

* `--dry-run / --no-dry-run`: Only list what would be deleted; delete nothing.  [default: no-dry-run]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow run`

Render a contract now: fetch its inputs, run the agent and save the draft; nothing is published.

**Usage**:

```console
$ hz workflow run [OPTIONS] {name}
```

**Arguments**:

* `name`: The contract&#x27;s name, or `&lt;namespace&gt;:&lt;name&gt;` (`team:` or `me:`) when both have it.  [required]

**Options**:

* `--dry-run / --no-dry-run`: Only fetch the inputs and show the packet; run no agent.  [default: no-dry-run]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow sent`

Record how much of a draft you changed before sending it; `hz workflow show` reports it.

**Usage**:

```console
$ hz workflow sent [OPTIONS] {name}
```

**Arguments**:

* `name`: The contract&#x27;s name, or `&lt;namespace&gt;:&lt;name&gt;` (`team:` or `me:`) when both have it.  [required]

**Options**:

* `--file <path>`: What you finally sent, saved as a text or Markdown file; or give --ref.
* `--ref <str>`: The document the approved version lives in, read through the documents capability.
* `--draft <path>`: The draft it was edited from; the contract&#x27;s newest draft if omitted.
* `--edit-minutes <float>`: Your own estimate of the minutes you spent editing; optional.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow show`

Show a contract as the daemon runs it: schedule, requirements, adapters, readiness, next steps.

**Usage**:

```console
$ hz workflow show [OPTIONS] {name}
```

**Arguments**:

* `name`: The contract&#x27;s name, or `&lt;namespace&gt;:&lt;name&gt;` (`team:` or `me:`) when both have it.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz zoom`

Read meetings from Zoom&#x27;s meeting-summary emails: the meetings capability.

**Usage**:

```console
$ hz zoom [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `info`: Show this plugin&#x27;s version, the...

### `hz zoom info`

Show this plugin&#x27;s version, the Heinzelmännchen it runs on and where its meetings come from.

**Usage**:

```console
$ hz zoom info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.
