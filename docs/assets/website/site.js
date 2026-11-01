/* Progressive enhancement: every catalog entry and command is useful without JavaScript. */
(() => {
  /* Lift up to two waypoints into free desktop lanes. All or nothing: any doubt leaves every one inline. */
  function placeGuides(helpers) {
    let lifted = false;
    const reset = () => {
      helpers.forEach(helper => {
        delete helper.dataset.gutter;
        ['--hz-lane-width', 'left', 'top', 'visibility'].forEach(name => helper.style.removeProperty(name));
      });
      lifted = false;
    };
    const rects = selector => [...document.querySelectorAll(selector)]
      .map(node => node.getBoundingClientRect()).filter(rect => rect.width && rect.height);
    // Free lanes beside the real article, navigation, header and footer; null when no lane is wide enough.
    const lanes = () => {
      const bounds = document.querySelector('.md-content')?.getBoundingClientRect();
      if (!bounds || innerWidth < 1100) return null;
      const navigation = rects('.md-sidebar__inner > .md-nav');
      const top = Math.max(150, ...rects('.md-header, .md-tabs').map(rect => rect.bottom + 24));
      const found = {};
      [['left', 16, bounds.left - 24], ['right', bounds.right + 24, innerWidth - 16]].forEach(([side, left, right]) => {
        // Prefer the outside of navigation; use its lower empty space only when needed.
        const neighbors = navigation.filter(rect => rect.left < right && rect.right > left);
        const outer = side === 'left' ? [left, Math.min(right, ...neighbors.map(rect => rect.left - 16))]
          : [Math.max(left, ...neighbors.map(rect => rect.right + 16)), right];
        const band = outer[1] - outer[0] >= 150 ? outer : [left, right];
        const width = Math.min(200, band[1] - band[0]);
        if (width < 150) return;
        const x = (band[0] + band[1] - width) / 2;
        let y = top;
        navigation.forEach(rect => {
          if (rect.left < x + width && rect.right > x && rect.bottom > top) y = Math.max(y, rect.bottom + 24);
        });
        found[side] = { x, y, width };
      });
      return found;
    };
    const follow = () => {
      // The footer pushes guides up; one pushed above its lane is hidden, never taken out of place.
      const footer = document.querySelector('.md-footer')?.getBoundingClientRect();
      const available = lanes();
      if (!available) { reset(); return false; }
      const used = {};
      for (const helper of helpers) {
        const wanted = helper.dataset.lane;
        const side = wanted === 'any'
          ? ['left', 'right'].filter(name => available[name])
              .sort((a, b) => (used[a] ?? available[a].y) - (used[b] ?? available[b].y))[0]
          : wanted;
        const lane = available[side];
        if (!lane) { reset(); return false; }
        helper.style.setProperty('--hz-lane-width', `${lane.width}px`);
        const y = used[side] ?? lane.y;
        const height = helper.getBoundingClientRect().height;
        if (y + height > innerHeight - 24) { reset(); return false; }
        const pushed = footer ? Math.min(y, footer.top - 24 - height) : y;
        helper.style.left = `${lane.x}px`;
        helper.style.top = `${pushed}px`;
        helper.style.visibility = pushed < y ? 'hidden' : '';
        used[side] = y + height + 24;
      }
      return true;
    };
    // Layout events decide whether guides are lifted; scrolling only follows the footer. Never reset on scroll:
    // inline waypoints sit mid-page, so toggling them would move the text being read, which keepPlace undoes.
    // A failed attempt is not repeated until the geometry that decided it changes (lifting resizes the article).
    const geometry = () => JSON.stringify([innerWidth, innerHeight, ...rects(
      '.md-content, .md-sidebar__inner > .md-nav, .md-header, .md-tabs').map(rect => [rect.left, rect.right, rect.height])]);
    const keepPlace = change => {
      const marker = scrollY && [...document.querySelectorAll('.md-content h2[id], .md-content h3[id]')]
        .find(heading => heading.getBoundingClientRect().bottom > 0);
      const before = marker && marker.getBoundingClientRect().top;
      change();
      if (marker) scrollBy(0, marker.getBoundingClientRect().top - before);
    };
    // Only a documentation page with no fixed place for its guide waits until it is two screens long.
    const needsLength = helpers.some(helper => helper.dataset.lane === 'any');
    let refused = '';
    const relayout = () => {
      const now = geometry();
      if (innerWidth < 1100) {
        if (lifted) reset();
        return;
      }
      if (!lifted) {
        if (now === refused || (needsLength && document.documentElement.scrollHeight < innerHeight * 2)) return;
        helpers.forEach(helper => {
          const style = getComputedStyle(helper);
          helper.dataset.slot = String(helper.offsetHeight + Number.parseFloat(style.marginTop) + Number.parseFloat(style.marginBottom));
          helper.dataset.gutter = 'true';
          // A guide in view must not wait for lazy loading, which some engines skip for fixed boxes.
          const art = helper.querySelector('img');
          if (art) art.loading = 'eager';
        });
        lifted = true;
        if (!follow()) { reset(); refused = now; }
        return;
      }
      const slots = helpers.reduce((total, helper) => total + Number(helper.dataset.slot || 0), 0);
      // Hysteresis: lift at two screens of text, release only below one and a half.
      const short = needsLength && document.documentElement.scrollHeight + slots < innerHeight * 1.5;
      if (short || !follow()) { reset(); refused = now; }
    };
    const layout = () => keepPlace(relayout);
    let scheduled = false;
    const later = callback => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; callback(); });
    };
    addEventListener('resize', () => later(layout), { passive: true });
    addEventListener('scroll', () => { if (lifted) later(() => keepPlace(follow)); }, { passive: true });
    const observer = new ResizeObserver(() => later(layout));
    document.querySelectorAll('.md-content, .md-sidebar__inner > .md-nav, .md-header, .md-tabs, .md-footer')
      .forEach(element => observer.observe(element));
    void document.fonts.ready.then(() => later(layout));
    helpers.forEach(helper => helper.querySelector('img')?.addEventListener('load', () => later(layout), { once: true }));
    layout();
  }


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
    const guides = [...document.querySelectorAll('.hz-page-helper[data-lane]')].slice(0, 2);
    if (guides.length && !guides[0].dataset.ready) {
      guides[0].dataset.ready = 'true';
      // The characters are decoration: if placing them fails, the rest of the page must still come alive.
      try { placeGuides(guides); } catch (error) { setTimeout(() => { throw error; }); }
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
    // The header no longer asks the GitHub API from the visitor's browser (docs/overrides/partials/source.html), so the
    // release the build looked up fills in at once. A widget that does carry a version still wins.
    const built = document.querySelector('meta[name="hz-latest-release"]')?.content;
    const versionFact = () => document.querySelector('.md-source__fact--version');
    const showVersion = () => {
      document.querySelectorAll('[data-hz-version]').forEach(link => {
        const text = versionFact()?.textContent.trim() || built;
        const label = `Latest release ${text}`;
        if (text && link.textContent !== label) link.textContent = label;
      });
    };
    showVersion();
    new MutationObserver(showVersion).observe(document.body, { childList: true, subtree: true });
    if (built && !versionFact()) {
      document.querySelectorAll('.md-source').forEach(source => {
        let facts = source.querySelector('.md-source__facts');
        if (!facts) {
          facts = document.createElement('ul');
          facts.className = 'md-source__facts';
          source.querySelector('.md-source__repository')?.append(facts);
        }
        const item = document.createElement('li');
        item.className = 'md-source__fact md-source__fact--version';
        item.textContent = built;
        facts.prepend(item);
      });
      showVersion();
    }
    document.querySelectorAll('[data-copy]').forEach(button => {
      if (button.dataset.ready) return;
      button.dataset.ready = 'true';
      button.hidden = false;
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
      // update() resizes the track: do it in the next frame, or the browser reports an undelivered ResizeObserver loop.
      const resize = new ResizeObserver(() => requestAnimationFrame(update));
      slides.forEach(slide => {
        resize.observe(slide);
        slide.querySelector('img').addEventListener('load', update);
      });
      update();
    });
    document.querySelectorAll('.hz-screenshots figure > a, .hz-product-story figure > a').forEach(link => {
      if (link.dataset.zoomReady) return;
      link.dataset.zoomReady = 'true';
      link.addEventListener('click', event => {
        if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (!HTMLDialogElement.prototype.showModal) return;
        event.preventDefault();
        openImage(link.querySelector('img'), link.href);
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
