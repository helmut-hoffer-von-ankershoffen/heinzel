# Published resident manifests

`record.py` boots a published wheel and records resident names, class definitions and lazy imports. The
cross-version tests use these manifests to check what a process can still import after updating in place.

## Core 0.9.55

- PyPI wheel `heinzel-0.9.55-py3-none-any.whl` has SHA-256
  `7ac762c36d1fa3f593206587e4a0f6f6e1926d5f8aea99c42ac8fd7c68bc9537`. The downloaded bytes matched the digest in
  the [PyPI release metadata](https://pypi.org/pypi/heinzel/0.9.55/json).
- Two runs of `record.py` against that local wheel with CPython 3.15.1, isolated uv environments and fresh private
  homes produced identical text. Each ran outside the checkout with its own Heinzel configuration, data, cache,
  state and log directories.
- The manifest records 382 resident modules, 295 defining modules and 163 lazy imports. Compressed with
  `gzip -n -9`, its SHA-256 is `32848014405fe733ec10330bf2e4dac704ed620bc6726d1b3186815887460317`.
