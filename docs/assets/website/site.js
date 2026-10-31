/* Progressive enhancement: every catalog entry and command is useful without JavaScript. */
(() => {
  function initialize() {
    const hero = document.querySelector('.hz-hero-art picture source');
    if (hero && !hero.dataset.ready) {
      hero.dataset.ready = 'true';
      const syncArtwork = () => {
        hero.media = document.body.dataset.mdColorScheme === 'slate' ? 'all' : 'not all';
      };
      syncArtwork();
      new MutationObserver(syncArtwork).observe(document.body, {
        attributes: true, attributeFilter: ['data-md-color-scheme'],
      });
    }
    document.querySelectorAll('[data-copy]').forEach(button => {
      if (button.dataset.ready) return;
      button.dataset.ready = 'true';
      button.addEventListener('click', async () => {
        const code = document.getElementById(button.dataset.copy);
        try {
          await navigator.clipboard.writeText(code.textContent);
          button.textContent = 'Copied';
          button.setAttribute('aria-label', 'Copied command');
        } catch {
          button.textContent = 'Select command';
          button.setAttribute('aria-label', 'Select command');
          const selection = window.getSelection();
          const range = document.createRange();
          range.selectNodeContents(code);
          selection.removeAllRanges();
          selection.addRange(range);
        }
      });
    });
    document.querySelectorAll('[data-play-soundscape]').forEach(button => {
      if (button.dataset.ready) return;
      button.dataset.ready = 'true';
      button.hidden = false;
      button.addEventListener('click', () => {
        const host = button.parentElement;
        const video = host.dataset.soundscape;
        if (!/^[A-Za-z0-9_-]{11}$/.test(video)) return;
        const player = document.createElement('iframe');
        player.src = `https://www.youtube-nocookie.com/embed/${video}?autoplay=1`;
        player.title = host.dataset.title;
        player.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
        player.referrerPolicy = 'strict-origin-when-cross-origin';
        player.allowFullscreen = true;
        host.replaceChildren(player);
      });
    });
    const catalog = document.querySelector('[data-catalog]');
    if (!catalog || catalog.dataset.ready) return;
    catalog.dataset.ready = 'true';
    catalog.querySelector('.hz-catalog-controls').hidden = false;
    const search = document.getElementById('plugin-search');
    const category = document.getElementById('plugin-category');
    const included = document.getElementById('plugin-included');
    const cards = [...catalog.querySelectorAll('[data-plugin]')];
    function filter() {
      const terms = search.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
      let count = 0;
      cards.forEach(card => {
        const matches = terms.every(term => card.dataset.search.includes(term)) &&
          (category.value === 'all' || category.value === card.dataset.category) &&
          (!included.checked || card.dataset.included === 'true');
        card.hidden = !matches;
        if (matches) count++;
      });
      document.getElementById('plugin-count').textContent = `${count} of ${cards.length} plugins`;
      document.getElementById('plugin-empty').hidden = count !== 0;
    }
    search.addEventListener('input', filter);
    category.addEventListener('change', filter);
    included.addEventListener('change', filter);
    document.getElementById('plugin-reset').addEventListener('click', () => {
      search.value = ''; category.value = 'all'; included.checked = false;
      filter(); search.focus();
    });
    filter();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
