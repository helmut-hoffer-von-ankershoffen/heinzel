# Publishing acceptance

A release is complete when its actual distribution installs cleanly and opens a working app. Preparing a workflow,
building a wheel or seeing a PyPI badge is only part of that evidence.

## Core PyPI release

The existing release PR, tag and GitHub release remain the source of the version. After review, CI and the published
GitHub release have been verified, a maintainer authorizes the irreversible registry upload:

```bash
gh workflow run pypi.yml --ref vX.Y.Z
```

Replace `vX.Y.Z` with that genuine release tag. The workflow refuses branches, missing releases, tags outside main,
version mismatches and incomplete core artifacts. It publishes only `heinzel`; workspace plugins retain their Git
installation sources until they have their own registered publishers and installation proof.

The trusted publisher is configured for the core repository, workflow `pypi.yml`, environment `pypi`. The environment
permits version tags only. No PyPI password or long-lived API token belongs in GitHub secrets or local configuration.
See [ADR 0079](adr/0079-core-pypi-trusted-publishing-is-isolated-from-builds.md) and the
[PyPI security model](https://docs.pypi.org/trusted-publishers/security-model/).

After upload, compare the registry filenames, versions and SHA-256 digests with the run's manifest. In a clean,
isolated tool directory, install the exact registry version, run `hz --version` and `hz system doctor --json`, open
`hz ui`, and exercise the release's changed interactions in a real browser. Keep the command output, artifact hashes,
workflow URL and browser evidence. A daemon or optional integration can legitimately need setup; the report must
show that state honestly rather than calling every unavailable integration a successful check.

PyPI versions/files cannot be replaced. If a run reports a partial or duplicate upload, inspect the registry's actual
files before retrying. Do not hide an uncertain outcome with `skip-existing` or delete and recreate a version.

## Installation walkthrough

Before advertising a new installation path or recording, demonstrate it on a clean machine/profile:

1. Install the exact release from its advertised source, launch the CLI, and show its version and useful help.
2. Start the browser app, choose a supported AI access method, and demonstrate a real first useful result.
3. Change settings, apply a restart when required, and verify the running instance actually changes.
4. Install an ordinary plugin, use its declared settings/navigation, and inspect health/errors and their recovery.
5. Demonstrate update and uninstall without losing personal configuration unexpectedly.

A packaged desktop installer is separate acceptance work: signed/notarized artifacts where the platform requires
it, installation, launch, runtime lifecycle, update, uninstall and the same UI interaction checks. The current
browser application and desktop proof of concept must not be described as an already shipped desktop installer.
Record the walkthrough only after these steps work; label the exact release and platform rather than stitching
together previews of features that are not in the installed artifact.
