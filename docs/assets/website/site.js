/* Progressive enhancement: every catalog entry and command is useful without JavaScript. */
(() => {
  function openImage(source, url) {
    const dialog = document.createElement('dialog');
    dialog.className = 'hz-image-viewer';
    dialog.setAttribute('aria-label', source.alt);
    const title = document.createElement('h2');
    title.textContent = source.alt;
    const toolbar = document.createElement('div');
    toolbar.className = 'hz-image-toolbar';
    const viewport = document.createElement('div');
    viewport.className = 'hz-image-viewport';
    viewport.tabIndex = 0;
    viewport.setAttribute('role', 'region');
    viewport.setAttribute('aria-label', 'Zoomed screenshot; scroll to explore');
    const image = document.createElement('img');
    image.referrerPolicy = 'no-referrer';
    image.alt = source.alt;
    image.src = url;
    const output = document.createElement('output');
    output.setAttribute('aria-live', 'polite');
    function button(label, action) {
      const control = document.createElement('button');
      control.type = 'button';
      control.textContent = label;
      control.addEventListener('click', action);
      toolbar.append(control);
      return control;
    }
    let scale = 1;
    const minimum = () => Math.min(.1, (viewport.clientWidth - 2) / image.naturalWidth,
      (viewport.clientHeight - 2) / image.naturalHeight);
    function render(value) {
      scale = Math.max(minimum(), Math.min(4, value));
      image.style.width = `${image.naturalWidth * scale}px`;
      output.textContent = `${Math.round(scale * 100)}%`;
      smaller.disabled = scale <= minimum();
      larger.disabled = scale >= 4;
    }
    function fit() {
      if (!image.naturalWidth) return;
      render(Math.min(1, (viewport.clientWidth - 2) / image.naturalWidth,
        (viewport.clientHeight - 2) / image.naturalHeight));
      viewport.scrollTo(0, 0);
    }
    const smaller = button('Zoom out', () => render(scale / 1.5));
    const larger = button('Zoom in', () => render(scale * 1.5));
    button('Fit', fit);
    button('Actual size', () => render(1));
    toolbar.append(output);
    const original = document.createElement('a');
    original.textContent = 'Open original';
    original.href = url;
    original.referrerPolicy = 'no-referrer';
    toolbar.append(original);
    const close = button('Close', () => dialog.close());
    viewport.append(image);
    dialog.append(title, toolbar, viewport);
    dialog.addEventListener('close', () => {
      dialog.remove();
      source.closest('a').focus();
    });
    image.addEventListener('load', fit);
    document.body.append(dialog);
    dialog.showModal();
    close.focus();
    if (image.complete) fit();
  }
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
      function fitTrack() {
        // Slide size is the input; changing the track never changes a slide's height.
        const style = getComputedStyle(track);
        const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
        const scrollbar = track.offsetHeight - track.clientHeight;
        const height = Math.ceil(slides[current].getBoundingClientRect().height + padding + scrollbar);
        if (track.style.height !== `${height}px`) track.style.height = `${height}px`;
      }
      function update() {
        current = slides.reduce((best, slide, index) =>
          Math.abs(position(slide) - track.scrollLeft) < Math.abs(position(slides[best]) - track.scrollLeft)
            ? index : best, 0);
        fitTrack();
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
      const resize = new ResizeObserver(update);
      slides.forEach(slide => {
        resize.observe(slide);
        const link = slide.querySelector('a');
        const image = link.querySelector('img');
        image.addEventListener('load', update);
        link.addEventListener('click', event => {
          if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          if (!HTMLDialogElement.prototype.showModal) return;
          event.preventDefault();
          openImage(image, link.href);
        });
      });
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
