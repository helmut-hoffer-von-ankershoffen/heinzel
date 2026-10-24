"""The module declaration and its credential, apart from `__init__.py` so the adapter can import the let."""

from heinzel import Module

module = Module("github", "Read pull requests and issues from GitHub: the code-host and tracker capabilities.")

TOKEN = module.credential(
    "github-token",
    purpose="GitHub token, read-only: a fine-grained token with Metadata, Pull requests and Issues read access "
    "(github.com/settings/personal-access-tokens). Optional for public repositories, which work anonymously at 60 "
    "requests an hour",
)
