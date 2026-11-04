# CI uv bootstrap

uv 0.13.7 passed main CI run 41745301666, including macOS setup job 126296915677.
The pinned setup-uv action resolves an exact version without a latest lookup, but still fetches
its artifact manifest. Supply a data URL from this reviewed checkout to remove that network
request too. Archive downloads, extraction and SHA-256 verification remain in the pinned action.
The four default artifacts cover the Linux ARM/x86 and macOS ARM/Intel CI runners.

Manifest source: https://github.com/astral-sh/versions/blob/09a7533c929d01fe2756a01b864a694a5ffe696a/v1/uv.ndjson

When updating uv, update the exact version and manifest together, verify release archive checksums,
and retain all caller cache and Python settings. The plugin template shares this action.
