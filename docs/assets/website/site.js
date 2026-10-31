/* Progressive enhancement: every catalog entry and command is useful without JavaScript. */
(() => {
  function placeHelpers(group) {
    const helpers = [...group.querySelectorAll('[data-heinzel-helper]')];
    const reset = () => helpers.forEach(helper => {
      helper.removeAttribute('data-gutter');
      helper.style.removeProperty('width');
      helper.style.removeProperty('left');
      helper.style.removeProperty('top');
    });
    reset();
    const content = document.querySelector('.md-content');
    if (!content || innerWidth < 1100 || group.getBoundingClientRect().top < innerHeight + 200) return;
    const bounds = content.getBoundingClientRect();
    const navigation = [...document.querySelectorAll('.md-sidebar__inner > .md-nav')]
      .map(node => node.getBoundingClientRect()).filter(rect => rect.width && rect.height);
    const header = [...document.querySelectorAll('.md-header, .md-tabs')]
      .map(node => node.getBoundingClientRect()).filter(rect => rect.width && rect.height);
    const footer = document.querySelector('.md-footer')?.getBoundingClientRect();
    const top = Math.max(150, ...header.map(rect => rect.bottom + 24));
    const bottom = Math.min(innerHeight - 24, footer ? footer.top - 24 : innerHeight - 24);
    const bands = [[16, bounds.left - 24], [bounds.right + 24, innerWidth - 16]];
    const lanes = bands.map(([left, right]) => {
      // Prefer the outside of navigation; use its lower empty space only when needed.
      const neighbors = navigation.filter(rect => rect.left < right && rect.right > left);
      const outer = left < bounds.left ? [left, Math.min(right, ...neighbors.map(rect => rect.left - 16))]
        : [Math.max(left, ...neighbors.map(rect => rect.right + 16)), right];
      const band = outer[1] - outer[0] >= 150 ? outer : [left, right];
      const width = Math.min(200, band[1] - band[0]);
      const x = (band[0] + band[1] - width) / 2;
      let y = top;
      navigation.forEach(rect => {
        if (rect.left < x + width && rect.right > x && rect.bottom > top) y = Math.max(y, rect.bottom + 24);
      });
      return { x, y, width };
    }).filter(lane => lane.width >= 150);
    for (const helper of helpers) {
      const lane = lanes.sort((a, b) => a.y - b.y)[0];
      if (!lane) { reset(); return; }
      helper.dataset.gutter = 'true';
      helper.style.width = `${lane.width}px`;
      const height = helper.getBoundingClientRect().height;
      if (lane.y + height > bottom) { reset(); return; }
      helper.style.left = `${lane.x}px`;
      helper.style.top = `${lane.y}px`;
      lane.y += height + 24;
    }
  }

  function initialize() {
    const helpers = document.querySelector('.hz-page-helpers');
    if (helpers && !helpers.dataset.ready) {
      helpers.dataset.ready = 'true';
      let scheduled = false;
      const update = () => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => { scheduled = false; placeHelpers(helpers); });
      };
      window.addEventListener('resize', update, { passive: true });
      window.addEventListener('scroll', update, { passive: true });
      const layoutObserver = new ResizeObserver(update);
      document.querySelectorAll('.md-content, .md-sidebar__inner > .md-nav, .md-header, .md-tabs, .md-footer')
        .forEach(element => layoutObserver.observe(element));
      document.fonts.ready.then(update);
      helpers.querySelectorAll('img').forEach(img => img.addEventListener('load', update, { once: true }));
      update();
    }
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
    document.querySelectorAll('[data-gallery]').forEach(gallery => {
      if (gallery.dataset.ready) return;
      gallery.dataset.ready = 'true';
      const track = gallery.querySelector('[data-gallery-track]');
      const slides = [...track.children];
      const previous = gallery.querySelector('[data-gallery-previous]');
      const next = gallery.querySelector('[data-gallery-next]');
      const counter = gallery.querySelector('[data-gallery-count]');
      gallery.querySelector('[data-gallery-controls]').hidden = slides.length < 2;
      const position = slide => slide.offsetLeft - slides[0].offsetLeft;
      let current = 0;
      function update() {
        current = slides.reduce((best, slide, index) =>
          Math.abs(position(slide) - track.scrollLeft) < Math.abs(position(slides[best]) - track.scrollLeft)
            ? index : best, 0);
        previous.disabled = current === 0;
        next.disabled = current === slides.length - 1;
        const label = `${current + 1} of ${slides.length}`;
        if (counter.textContent !== label) counter.textContent = label;
      }
      function go(index) {
        const target = Math.max(0, Math.min(slides.length - 1, index));
        track.scrollTo({
          left: position(slides[target]),
          behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
        });
      }
      previous.addEventListener('click', () => go(current - 1));
      next.addEventListener('click', () => go(current + 1));
      track.addEventListener('scroll', update, { passive: true });
      track.addEventListener('keydown', event => {
        if (event.target !== track) return;
        const targets = { ArrowLeft: current - 1, ArrowRight: current + 1, Home: 0, End: slides.length - 1 };
        if (!(event.key in targets)) return;
        event.preventDefault();
        go(targets[event.key]);
      });
      new ResizeObserver(update).observe(track);
      update();
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
