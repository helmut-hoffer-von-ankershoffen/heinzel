# CI uv bootstrap

uv 0.13.7 was installed successfully in macOS setup job 126296915677 of main CI run
41745301666. That run failed in another job during remote manifest fetching.
The pinned setup-uv action resolves an exact version without a latest lookup, but still fetches
its artifact manifest. Supply a data URL from this reviewed checkout to remove that network
request too. Archive downloads, extraction and SHA-256 verification remain in the pinned action.
The four default artifacts cover the Linux ARM/x86 and macOS ARM/Intel CI runners.

Keep the manifest as one NDJSON record: the pinned action parses each nonblank line as JSON.

Manifest source: https://github.com/astral-sh/versions/blob/09a7533c929d01fe2756a01b864a694a5ffe696a/v1/uv.ndjson

When updating uv, update the exact version and manifest together, verify release archive checksums,
and retain all caller cache and Python settings. The plugin template shares this action.
