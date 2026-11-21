(() => {
  'use strict';
  const panel = document.querySelector('[data-panel="mindmap"]');
  const svgEl = panel?.querySelector('#markmap-svg');
  const data = document.getElementById('yano-mindmap-data');
  if (!panel || !svgEl || !data) return;
  const MINDMAP_MD = JSON.parse(data.textContent);
  const sourceData = JSON.parse(document.getElementById('linkread-mindmap-links')?.textContent || '{}');
  const cueRanges = Array.from(document.querySelectorAll('.reading-paragraph'), paragraph =>
    [Number(paragraph.dataset.cueStart), Number(paragraph.dataset.cueEnd)]);
  const sourceLinks = new Map(Object.entries(sourceData).filter(([path, cue]) =>
    /^0(?:\.[0-9]+){1,5}$/.test(path) && Number.isSafeInteger(cue) && cue >= 0 &&
    cueRanges.some(([start, end]) => start <= cue && end >= cue)));
  const position = window.linkreadReadingPosition;
  const signature = window.LinkReadReadingPosition.fingerprint(MINDMAP_MD.replace(/\r\n/g, '\n').trim());
  const colors = ['#7c5cfc','#0ea5e9','#f59e0b','#22c55e','#ef4444','#8b5cf6'];
  const wrap = svgEl.closest('.mindmap-wrap');
  const fullscreenBtn = wrap?.querySelector('[data-mindmap-fullscreen]');
  const sourceBar = wrap.querySelector('[data-mindmap-source]');
  let selectedSource = null;
  let instance = null, fallback = null, started = false, ready = false, rootData = null;
  let mode = 'overview', lastViewport = null, operation = 0, resizeFrame = 0;
  let exitingFullscreen = false, wasNativeFullscreen = false, nativeEscapePending = false, nativeEscapeReleased = false, nativeEscapeTimer = 0;
  let lastSize = {width: 0, height: 0}, pendingSaved = position?.getMindmap(signature) || null;
  const isVisible = () => !panel.hidden && getComputedStyle(panel).display !== 'none';
  const isFullscreen = () => document.fullscreenElement === wrap || wrap.classList.contains('is-fullscreen');
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const visibleCenter = window.LinkReadReadingPosition.visibleCenter;
  const dimensions = () => {
    const {width, height} = svgEl.getBoundingClientRect();
    return {width, height};
  };
  function selectSource(path, label) {
    if (!sourceLinks.has(path)) return;
    selectedSource = path;
    sourceBar.hidden = false;
    sourceBar.querySelector('[data-mindmap-source-label]').textContent = label;
    svgEl.querySelectorAll('[data-source-path]').forEach(element =>
      element.setAttribute('aria-pressed', String(element.dataset.sourcePath === path)));
  }
  svgEl.addEventListener('click', event => {
    const label = event.target.closest('[data-source-path]');
    if (!label) return;
    event.preventDefault(); event.stopPropagation();
    selectSource(label.dataset.sourcePath, label.textContent);
  });
  // Buttons in foreignObject labels should not start D3's drag gesture.
  svgEl.addEventListener('mousedown', event => {
    if (event.target.closest('[data-source-path]')) event.stopPropagation();
  }, true);
  sourceBar.querySelector('[data-mindmap-read]').addEventListener('click', async () => {
    const cue = sourceLinks.get(selectedSource);
    if (!Number.isSafeInteger(cue)) return;
    remember(false);
    await exitMapFullscreen(false);
    if (panel.hidden) return;
    window.dispatchEvent(new CustomEvent('linkread-source-jump', {detail: {cue}}));
  });
  if (sourceLinks.size) wrap.querySelector('.mindmap-hint').textContent = 'Select a linked idea to read · Use circles to expand · Drag to move';
  function setMode(value) {
    mode = value;
    wrap.querySelector('[data-mindmap-overview]')?.setAttribute('aria-pressed', String(mode === 'overview'));
    wrap.querySelector('[data-mindmap-expand]')?.setAttribute('aria-pressed', String(mode === 'expanded'));
  }
  function foldedPaths(node, result = []) {
    if (node.children?.length && node.payload?.fold) result.push(node.payload.positionPath);
    node.children?.forEach(child => foldedPaths(child, result));
    return result;
  }
  function markmapViewport() {
    const {width, height} = dimensions();
    if (!instance || !width || !height || !window.d3?.zoomTransform) return lastViewport;
    const transform = window.d3.zoomTransform(svgEl);
    if (!Number.isFinite(transform.k) || transform.k <= 0) return lastViewport;
    return {engine: 'markmap', cx: (width / 2 - transform.x) / transform.k,
      cy: (height / 2 - transform.y) / transform.k, scale: clamp(transform.k, .02, 20)};
  }
  function captureMap() {
    if (!ready) return pendingSaved;
    let folded, viewport;
    if (instance) {
      folded = foldedPaths(instance.state.data);
      viewport = markmapViewport();
    } else if (fallback) {
      ({folded, viewport} = fallback.capture());
    } else return null;
    // Large legacy maps remain readable. Do not truncate their fold state.
    if (folded.length > 2048) return null;
    if (viewport) lastViewport = viewport;
    return {signature, folded, mode, viewport: viewport || null};
  }
  function remember(save = true) {
    const current = captureMap();
    if (current) position?.setMindmap(current, save);
  }
  function applyViewport(view) {
    if (!view || !isVisible()) return false;
    if (instance && view.engine === 'markmap') {
      const {width, height} = dimensions(), bounds = instance.state.rect;
      if (!width || !height || !(bounds.x2 > bounds.x1) || !(bounds.y2 > bounds.y1)) return false;
      const scale = clamp(view.scale, .02, 20);
      // Changed window dimensions keep the same graph centre. Clamp only if
      // a collapsed tree would otherwise leave the whole graph out of view.
      const cx = visibleCenter(view.cx, bounds.x1, bounds.x2, width / (2 * scale));
      const cy = visibleCenter(view.cy, bounds.y1, bounds.y2, height / (2 * scale));
      const transform = window.d3.zoomIdentity.translate(width / 2 - cx * scale, height / 2 - cy * scale).scale(scale);
      instance.svg.interrupt();
      instance.svg.call(instance.zoom.transform, transform);
      lastViewport = {engine: 'markmap', cx, cy, scale};
      lastSize = {width, height};
      return true;
    }
    if (fallback && view.engine === 'fallback') {
      fallback.applyViewport(view);
      lastViewport = fallback.capture().viewport;
      lastSize = dimensions();
      return true;
    }
    return false;
  }
  function fitMap() {
    if (instance) {
      const {x1, x2, y1, y2} = instance.state.rect;
      const {width, height} = dimensions();
      if (!(width > 0 && height > 0 && x2 > x1 && y2 > y1)) return;
      const {fitRatio, maxInitialScale} = instance.options;
      // Apply the fitted transform in one step so resize and position capture
      // cannot pick up an intermediate zoom transform.
      applyViewport({
        engine: 'markmap', cx: (x1 + x2) / 2, cy: (y1 + y2) / 2,
        scale: Math.min(width / (x2 - x1) * fitRatio, height / (y2 - y1) * fitRatio, maxInitialScale),
      });
    } else fallback?.fit();
  }
  function fitView(save = true) {
    const current = ++operation;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!isVisible() || current !== operation) return;
      fitMap();
      lastSize = dimensions();
      remember(save);
    }));
  }
  function resizeView() {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!ready || !isVisible()) return;
      const size = dimensions();
      if (!size.width || !size.height || size.width === lastSize.width && size.height === lastSize.height) return;
      if (!applyViewport(lastViewport)) fitView(false);
      lastSize = size;
      remember(false);
    }));
  }
  function syncFullscreen() {
    const native = document.fullscreenElement === wrap;
    if (native && !wasNativeFullscreen) {
      nativeEscapeReleased = false;
      nativeEscapePending = false;
      clearTimeout(nativeEscapeTimer);
    }
    if (wasNativeFullscreen && !native) {
      // Some engines exit native fullscreen before delivering Escape to JS.
      // WebKit can deliver keyup before the delayed fullscreenchange. Do not
      // re-arm a consumed key after release and swallow the next deliberate press.
      if (!exitingFullscreen && !nativeEscapeReleased) {
        nativeEscapePending = true;
        clearTimeout(nativeEscapeTimer);
        nativeEscapeTimer = setTimeout(() => { nativeEscapePending = false; }, 500);
      }
      fullscreenBtn?.focus({preventScroll: true});
    }
    wasNativeFullscreen = native;
    const active = isFullscreen();
    if (fullscreenBtn) {
      fullscreenBtn.textContent = active ? 'Exit fullscreen' : 'Fullscreen';
      fullscreenBtn.setAttribute('aria-label', active ? 'Exit mindmap fullscreen' : 'View mindmap fullscreen');
      fullscreenBtn.setAttribute('title', active ? 'Exit fullscreen' : 'View mindmap fullscreen');
      fullscreenBtn.setAttribute('aria-pressed', String(active));
    }
    document.body.classList.toggle('mindmap-fullscreen-open', wrap.classList.contains('is-fullscreen'));
    resizeView();
  }
  async function exitMapFullscreen(restoreFocus = true) {
    if (exitingFullscreen) return;
    exitingFullscreen = true;
    remember(false);
    try {
      if (document.fullscreenElement === wrap && document.exitFullscreen) {
        try { await document.exitFullscreen(); } catch {}
      }
      wrap.classList.remove('is-fullscreen');
      syncFullscreen();
      if (restoreFocus) fullscreenBtn?.focus({preventScroll: true});
    } finally { exitingFullscreen = false; }
  }
  function closeMindmapLayer() {
    if (nativeEscapePending) { nativeEscapePending = false; return true; }
    if (!isFullscreen() && !exitingFullscreen) return false;
    void exitMapFullscreen();
    return true;
  }
  window.linkreadCloseMindmapLayer = closeMindmapLayer;
  async function toggleFullscreen() {
    remember(false);
    if (isFullscreen()) {
      await exitMapFullscreen();
    } else {
      let entered = false;
      if (typeof wrap.requestFullscreen === 'function') {
        try { await wrap.requestFullscreen(); entered = true; } catch {}
      }
      if (!entered) wrap.classList.add('is-fullscreen');
    }
    syncFullscreen();
  }
  fullscreenBtn?.addEventListener('click', event => {
    event.preventDefault(); event.stopPropagation();
    void toggleFullscreen();
  });
  document.addEventListener('fullscreenchange', syncFullscreen);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !event.repeat) nativeEscapeReleased = false;
  }, true);
  document.addEventListener('keydown', event => {
    // The full reader owns layer ordering. Retain a safe standalone fallback.
    if (window.linkreadCloseReaderLayer || event.key !== 'Escape' || event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
    if (event.repeat) { event.preventDefault(); return; }
    if (closeMindmapLayer()) { event.preventDefault(); event.stopImmediatePropagation(); }
  });
  document.addEventListener('keyup', event => {
    if (event.key === 'Escape') {
      if (wasNativeFullscreen) nativeEscapeReleased = true;
      nativeEscapePending = false;
      clearTimeout(nativeEscapeTimer);
    }
  });
  if (window.ResizeObserver) new ResizeObserver(resizeView).observe(svgEl);
  else window.addEventListener('resize', resizeView);
  function plainTextLabels(node, labels, path = '0') {
    const label = document.createElement(sourceLinks.has(path) ? 'button' : 'span');
    label.textContent = labels[Number(node.content) - 1] || '';
    if (sourceLinks.has(path)) {
      label.type = 'button'; label.className = 'mindmap-source-label'; label.dataset.sourcePath = path;
      label.setAttribute('aria-pressed', String(selectedSource === path));
      label.setAttribute('aria-label', 'Show source for ' + label.textContent);
      label.title = 'Show source section';
      node.content = label.outerHTML;
    } else node.content = label.innerHTML;
    node.children?.forEach((child, index) => plainTextLabels(child, labels, path + '.' + index));
  }
  function treeAtDepth(node, depth = 0, expanded = false, branch = 0, path = '0', saved = null) {
    const fold = saved ? saved.folded.includes(path) : !expanded && depth >= 2;
    return {content: node.content, payload: {...(node.payload || {}), fold: fold ? 1 : 0, branch, positionPath: path},
      children: (node.children || []).map((child, index) => treeAtDepth(child, depth + 1, expanded,
        depth === 0 ? index : branch, path + '.' + index, saved))};
  }
  async function restoreMap(saved) {
    pendingSaved = saved?.signature === signature ? saved : null;
    if (!pendingSaved || !started || !ready) return;
    const current = ++operation;
    setMode(pendingSaved.mode);
    if (instance && rootData) await instance.setData(treeAtDepth(rootData, 0, false, 0, '0', pendingSaved));
    else fallback?.restore(pendingSaved);
    if (current !== operation) return;
    if (!applyViewport(pendingSaved.viewport)) fitView(false);
    remember(false);
  }
  async function setExpansion(expanded) {
    renderMindmap();
    const current = ++operation;
    pendingSaved = null;
    setMode(expanded ? 'expanded' : 'overview');
    if (instance && rootData) await instance.setData(treeAtDepth(rootData, 0, expanded));
    else if (expanded) fallback?.expand();
    else fallback?.overview();
    if (instance || fallback) ready = true;
    if (current === operation) fitView();
  }
  wrap.querySelector('[data-mindmap-overview]')?.addEventListener('click', () => { void setExpansion(false); });
  wrap.querySelector('[data-mindmap-expand]')?.addEventListener('click', () => { void setExpansion(true); });
  wrap.querySelector('[data-mindmap-fit]')?.addEventListener('click', () => fitView());
  position?.registerMindmap({capture: captureMap, restore: restoreMap});
  function renderMindmap() {
    if (!isVisible()) return;
    if (started) { resizeView(); return; }
    started = true;
    pendingSaved = position?.getMindmap(signature) || pendingSaved;
    try {
      if (window.markmap?.Transformer && window.markmap?.Markmap) {
        const {Transformer, Markmap} = window.markmap;
        const labels = [];
        const outline = MINDMAP_MD.trim().split(/\r?\n/).flatMap(line => {
          const heading = /^(#{1,6})\s+(.+)$/.exec(line.trim());
          return heading ? [heading[1] + ' ' + labels.push(heading[2])] : [];
        }).join('\n');
        const {root} = new Transformer().transform(outline);
        plainTextLabels(root, labels);
        rootData = root;
        instance = Markmap.create(svgEl, {
          autoFit: false,
          color: node => colors[(node.payload?.branch ?? 0) % colors.length] || colors[0],
          duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 220,
          initialExpandLevel: -1, maxWidth: 220, spacingHorizontal: 55,
          spacingVertical: 9, paddingX: 16, scrollForPan: true,
        });
        const toggle = instance.toggleNode.bind(instance);
        instance.toggleNode = async (...args) => {
          const current = ++operation;
          const view = markmapViewport();
          await toggle(...args);
          if (current !== operation) return;
          setMode('custom');
          applyViewport(view);
          remember();
        };
        instance.zoom.on('end.linkread-position', event => {
          if (!ready) return;
          lastViewport = markmapViewport();
          remember(!!event.sourceEvent);
        });
        // Markmap's wheel-for-pan path applies a programmatic D3 transform,
        // which has no sourceEvent. Persist the user's pan explicitly.
        svgEl.addEventListener('wheel', () => requestAnimationFrame(() => remember()), {passive: true});
        const current = ++operation;
        setMode(pendingSaved?.mode || 'overview');
        void instance.setData(treeAtDepth(root, 0, false, 0, '0', pendingSaved)).then(() => {
          if (current !== operation) return;
          if (!applyViewport(pendingSaved?.viewport)) fitMap();
          ready = true;
          lastSize = dimensions();
          remember(false);
        });
        return;
      }
    } catch (error) { console.warn('Markmap failed, using offline SVG map', error); instance = null; }
    fallback = buildOfflineMap(MINDMAP_MD, svgEl, colors, pendingSaved);
    ready = true;
    setMode(pendingSaved?.mode || 'overview');
    lastSize = dimensions();
    remember(false);
  }
  function buildOfflineMap(source, el, palette, saved) {
    const NS = 'http://www.w3.org/2000/svg';
    const node = (tag, attrs, content) => {
      const element = document.createElementNS(NS, tag);
      for (const [key, value] of Object.entries(attrs || {})) element.setAttribute(key, String(value));
      if (content !== undefined) element.textContent = content;
      return element;
    };
    const stack = [], nodes = [];
    for (const line of source.split(/\r?\n/)) {
      const match = /^(#{1,6})\s+(.+)$/.exec(line.trim());
      if (!match) continue;
      const entry = {text: match[2], level: match[1].length, children: [], closed: match[1].length >= 3};
      while (stack.length && stack[stack.length - 1].level >= entry.level) stack.pop();
      if (stack.length) stack[stack.length - 1].children.push(entry); else nodes.push(entry);
      stack.push(entry);
    }
    if (nodes.length !== 1) {
      el.setAttribute('aria-label', 'Invalid mindmap structure');
      return {fit() {}, expand() {}, overview() {}, restore() {}, applyViewport() {}, capture() {return {folded: [], viewport: null};}};
    }
    const root = nodes[0];
    const assign = (entry, path) => {
      entry.path = path;
      if (saved) entry.closed = saved.folded.includes(path);
      entry.children.forEach((child, index) => assign(child, path + '.' + index));
    };
    assign(root, '0');
    let view = null, bounds = null, pointer = null, scale = 1;
    const updateView = () => {
      if (view) el.setAttribute('viewBox', [view.x, view.y, view.w, view.h].join(' '));
    };
    const viewport = () => view ? {engine: 'fallback', cx: view.x + view.w / 2, cy: view.y + view.h / 2, scale} : null;
    const apply = value => {
      const {width, height} = dimensions();
      if (!bounds || !width || !height || !value) return;
      scale = clamp(value.scale, .02, 20);
      const cx = visibleCenter(value.cx, 0, bounds.w, width / (2 * scale));
      const cy = visibleCenter(value.cy, 0, bounds.h, height / (2 * scale));
      const w = width / scale, h = height / scale;
      view = {x: cx - w / 2, y: cy - h / 2, w, h};
      updateView();
    };
    const fit = () => {
      const {width, height} = dimensions();
      if (!bounds || !width || !height) return;
      apply({cx: bounds.w / 2, cy: bounds.h / 2, scale: Math.min(width / bounds.w, height / bounds.h, 2) * .94});
    };
    const draw = () => {
      const previous = viewport();
      el.replaceChildren();
      let leaf = 0, maxDepth = 0;
      const place = (entry, depth, branch) => {
        entry.depth = depth; entry.branch = branch; entry.x = depth * 295 + 32;
        maxDepth = Math.max(maxDepth, depth);
        const sub = entry.closed ? [] : entry.children;
        sub.forEach((child, index) => place(child, depth + 1, depth === 0 ? index : branch));
        entry.y = sub.length ? (sub[0].y + sub[sub.length - 1].y) / 2 : leaf++ * 49 + 52;
        entry.width = Math.min(280, Math.max(100, [...entry.text].length * 14 + 20));
      };
      place(root, 0, 0);
      bounds = {w: Math.max(890, (maxDepth + 1) * 295 + 70), h: Math.max(380, leaf * 49 + 100)};
      const edges = node('g', {fill: 'none', 'stroke-width': 2, 'stroke-opacity': .7}), labels = node('g', {});
      const visit = entry => {
        const color = palette[entry.branch % palette.length];
        for (const child of entry.closed ? [] : entry.children) {
          const endX = child.x - 8, startX = entry.x + entry.width - 8;
          edges.append(node('path', {d: 'M ' + startX + ' ' + (entry.y + 7) + ' C ' + (startX + 68) + ' ' + (entry.y + 7)
            + ', ' + (endX - 72) + ' ' + (child.y + 7) + ', ' + endX + ' ' + (child.y + 7), stroke: palette[child.branch % palette.length]}));
          visit(child);
        }
        const group = node('g', {'class': 'yano-fallback-node',
          role: 'treeitem', 'aria-label': entry.text, ...(entry.children.length ? {'aria-expanded': String(!entry.closed)} : {})});
        const label = node('text', {x: entry.x, y: entry.y, 'font-weight': entry.depth < 2 ? 650 : 450}, entry.text);
        if (sourceLinks.has(entry.path)) {
          label.classList.add('mindmap-source-label'); label.dataset.sourcePath = entry.path;
          label.setAttribute('tabindex', '0'); label.setAttribute('role', 'button');
          label.setAttribute('aria-label', 'Show source for ' + entry.text);
          label.setAttribute('aria-pressed', String(selectedSource === entry.path));
          label.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault(); event.stopPropagation(); selectSource(entry.path, entry.text);
            }
          });
        }
        group.append(label,
          node('path', {d: 'M ' + entry.x + ' ' + (entry.y + 7) + ' h ' + Math.max(60, entry.width - 24),
            stroke: color, 'stroke-width': entry.depth === 0 ? 3 : 1.7, opacity: .92}));
        if (entry.children.length) {
          const control = node('g', {class: 'yano-fallback-toggle', tabindex: '0', role: 'button',
            'aria-label': (entry.closed ? 'Expand ' : 'Collapse ') + entry.text});
          control.append(node('circle', {cx: entry.x + entry.width - 8, cy: entry.y + 5, r: 7,
            fill: 'var(--surface, #f7f7f5)', stroke: color, 'stroke-width': 1.6}),
          node('text', {x: entry.x + entry.width - 8, y: entry.y + 9, 'text-anchor': 'middle', 'font-size': 12,
            'font-weight': 650}, entry.closed ? '+' : '−'));
          const toggle = () => { entry.closed = !entry.closed; setMode('custom'); draw(); remember(); };
          control.addEventListener('click', event => { event.stopPropagation(); toggle(); });
          control.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); }
          });
          group.append(control);
        }
        labels.append(group);
      };
      visit(root); el.append(edges, labels);
      if (previous) apply(previous); else fit();
    };
    el.addEventListener('wheel', event => {
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height || !view) return;
      const px = (event.clientX - rect.left) / rect.width, py = (event.clientY - rect.top) / rect.height;
      const factor = event.deltaY > 0 ? 1.16 : .86, oldW = view.w, oldH = view.h;
      scale = clamp(scale / factor, .02, 20);
      view.w = rect.width / scale; view.h = rect.height / scale;
      view.x += (oldW - view.w) * px; view.y += (oldH - view.h) * py;
      updateView(); remember();
    }, {passive: false});
    el.addEventListener('pointerdown', event => {
      if (event.target.closest('.yano-fallback-node') || !view) return;
      pointer = {id: event.pointerId, x: event.clientX, y: event.clientY, ox: view.x, oy: view.y};
      el.setPointerCapture(event.pointerId);
    });
    el.addEventListener('pointermove', event => {
      if (!pointer || pointer.id !== event.pointerId) return;
      const rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      view.x = pointer.ox - (event.clientX - pointer.x) * view.w / rect.width;
      view.y = pointer.oy - (event.clientY - pointer.y) * view.h / rect.height;
      updateView();
    });
    el.addEventListener('pointerup', () => { pointer = null; remember(); });
    el.addEventListener('pointercancel', () => { pointer = null; remember(); });
    const walk = (entry, action) => { action(entry); entry.children.forEach(child => walk(child, action)); };
    draw();
    if (saved?.viewport?.engine === 'fallback') apply(saved.viewport);
    return {
      fit, applyViewport: apply,
      expand() { walk(root, entry => { entry.closed = false; }); draw(); },
      overview() { walk(root, entry => { entry.closed = entry.level >= 3; }); draw(); },
      restore(value) { walk(root, entry => { entry.closed = value.folded.includes(entry.path); }); draw(); },
      capture() {
        const folded = [];
        walk(root, entry => { if (entry.children.length && entry.closed) folded.push(entry.path); });
        return {folded, viewport: viewport()};
      },
    };
  }
  new MutationObserver(() => { if (isVisible()) renderMindmap(); })
    .observe(panel, {attributes: true, attributeFilter: ['hidden', 'class', 'style']});
  if (document.readyState === 'complete') renderMindmap();
  else window.addEventListener('load', renderMindmap);
})();
