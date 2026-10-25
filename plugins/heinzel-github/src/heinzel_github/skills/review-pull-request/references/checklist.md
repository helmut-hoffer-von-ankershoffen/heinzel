# Pull request checklist

- **Intent**: does the change do what the title and description say, and nothing else?
- **Correctness**: edge cases, error paths, empty inputs, time zones, concurrency.
- **Tests**: does a test fail without the change? Are the new branches covered?
- **Security**: input that reaches a shell, a path, a query or a template; secrets in code or logs; new permissions.
- **Interfaces**: changed public APIs, configuration or data formats; is the change backwards compatible, or marked
  as breaking?
- **Operations**: migrations, feature flags, rollbacks, what an on-call person sees when it fails.
- **Readability**: names, comments that explain why, dead code left behind.
- **Docs and changelog**: what a user or another developer needs to know.
