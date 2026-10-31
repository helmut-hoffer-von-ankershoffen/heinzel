---
title: Plugins & themes
description: Discover capabilities and themes for Heinzelmännchen, with immutable source links and compatibility metadata.
hide:
  - navigation
  - toc
---

<p class="hz-eyebrow">A small core. A growing world.</p>

# Make Heinzelmännchen yours.

<p class="hz-lead">Clocks, connections, context and character. Explore independently owned plugins, then build the one your team needs.</p>

[Install the standard app](guides/install.md) · [Create a plugin](PLUGIN_AUTHORING.md) · [How discovery works](guides/catalog.md)

<div class="hz-catalog" data-catalog>
<div class="hz-catalog-controls" hidden>
<label for="plugin-search">Find a plugin<input id="plugin-search" type="search" placeholder="Search capabilities, names, themes…" autocomplete="off"></label>
<label for="plugin-category">Category<select id="plugin-category"><option value="all">All categories</option><option value="utilities">Utilities</option><option value="connectors">Connectors</option><option value="themes">Themes</option><option value="reference">Reference examples</option></select></label>
<label class="hz-check"><input id="plugin-included" type="checkbox">Included in the app</label>
<button type="button" id="plugin-reset">Reset filters</button>
</div>
<p id="plugin-count" role="status" aria-live="polite"></p>
<p id="plugin-empty" hidden>No plugins match. Try a different search or reset the filters.</p>

<!-- hz:catalog -->

</div>

## A different atmosphere.

Themes can own light and dark palettes, artwork and soundscapes. These illustrations use palettes read from the owning source; they are previews, not screenshots of a running plugin.

<!-- hz:themes -->

## From your repository to this catalog.

Put your plugin in its own public repository, add the `heinzel-plugin` GitHub topic, and declare its `heinzel.modules` entry point. No core registration is needed. Private plugins use authenticated CLI discovery and never appear on this public site.

[Publish your first plugin →](PLUGIN_AUTHORING.md) · [Add showcase metadata →](guides/catalog.md)

<!-- hz:freshness -->
