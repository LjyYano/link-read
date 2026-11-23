// Small, versioned reading-position records. No source text or credentials are stored.
((root) => {
  'use strict';
  const TABS = ['summary', 'mindmap', 'transcript', 'keywords', 'highlights', 'shownotes'];
  const TOKEN = /^p1-[0-9a-f]{16}-[0-9]+$/;
  const PATH = /^0(?:\.[0-9]+){0,6}$/;
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const finite = (value, min, max) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
  const integer = (value, max) => Number.isInteger(value) && value >= 0 && value <= max;
  const keys = (value, names) => object(value) && Object.keys(value).every(key => names.includes(key));
  const token = value => typeof value === 'string' && value.length <= 64 && TOKEN.test(value);
  function fingerprint(value) {
    let first = 2166136261, second = 3339675911;
    for (let index = 0; index < value.length; index++) {
      const char = value.charCodeAt(index);
      first = Math.imul(first ^ char, 16777619);
      second = Math.imul(second ^ char, 2246822519);
    }
    const hex = value => (value >>> 0).toString(16).padStart(8, '0');
    return 'p1-' + hex(first) + hex(second) + '-' + value.length;
  }
  // Punctuation-only proofreading and paragraph regrouping keep the source identity.
  const lexical = value => value.replace(/[\p{P}\p{White_Space}\u001c-\u001f]/gu, '');
  // A panned viewport may have its centre outside a small graph while the
  // whole graph remains visible. Correct only a completely disjoint view.
  const visibleCenter = (center, low, high, halfExtent) =>
    center + halfExtent < low || center - halfExtent > high ? Math.max(low, Math.min(high, center)) : center;
  function validAnchor(value) {
    if (!object(value)) return false;
    if (value.kind === 'top') return keys(value, ['kind']);
    if (!token(value.token) || !integer(value.occurrence, 1000000)) return false;
    if (value.kind === 'block') return keys(value, ['kind', 'token', 'occurrence']);
    return value.kind === 'cue' && keys(value, ['kind', 'cue', 'endCue', 'token', 'occurrence'])
      && integer(value.cue, 10000000) && integer(value.endCue, 10000000) && value.endCue >= value.cue;
  }
  function validPosition(value) {
    return keys(value, ['signature', 'anchor', 'offset', 'ratio']) && token(value.signature)
      && validAnchor(value.anchor) && finite(value.offset, -100000, 100000) && finite(value.ratio, 0, 1);
  }
  function validMindmap(value) {
    if (value === null) return true;
    if (!keys(value, ['signature', 'folded', 'mode', 'viewport']) || !token(value.signature)
      || !['overview', 'expanded', 'custom'].includes(value.mode) || !Array.isArray(value.folded)
      || value.folded.length > 2048 || !value.folded.every(path => typeof path === 'string' && path.length <= 64 && PATH.test(path))) return false;
    const view = value.viewport;
    return view === null || (keys(view, ['engine', 'cx', 'cy', 'scale'])
      && ['markmap', 'fallback'].includes(view.engine) && finite(view.cx, -10000000, 10000000)
      && finite(view.cy, -10000000, 10000000) && finite(view.scale, .02, 20));
  }
  function sanitize(value) {
    if (!keys(value, ['version', 'updatedAt', 'activeTab', 'tabs', 'mindmap']) || value.version !== 1
      || !integer(value.updatedAt, 1000000000000000) || !TABS.includes(value.activeTab)
      || !keys(value.tabs, TABS) || !Object.values(value.tabs).every(validPosition) || !validMindmap(value.mindmap)) return null;
    try {
      const json = JSON.stringify(value);
      if (json.length > 262144) return null;
      return JSON.parse(json);
    } catch { return null; }
  }
  function resolveAnchor(position, anchors, signature) {
    if (!validPosition(position) || position.anchor.kind === 'top') return null;
    const saved = position.anchor;
    const matching = anchors.filter(anchor => anchor.token === saved.token);
    if (saved.kind === 'cue' && position.signature === signature) {
      const containing = anchors.filter(anchor => anchor.kind === 'cue' && anchor.cue <= saved.cue && anchor.endCue >= saved.cue);
      const exact = containing.find(anchor => anchor.token === saved.token && anchor.occurrence === saved.occurrence);
      if (exact) return { anchor: exact, within: true };
      if (containing.length) return { anchor: containing[0], within: false };
      return null;
    }
    // After actual content changes, a repeated phrase is not a reliable anchor.
    const exact = position.signature === signature
      ? matching.find(anchor => anchor.occurrence === saved.occurrence)
      : matching.length === 1 ? matching[0] : null;
    return exact ? { anchor: exact, within: true } : null;
  }
  function create(options) {
    const document = root.document, key = options.key;
    if (!document || typeof key !== 'string' || !key || key.length > 2048) return null;
    const $ = selector => document.querySelector(selector);
    const storageKey = 'linkread-reading-position-v1:' + fingerprint(key);
    const nonce = new Uint32Array(4);
    try { root.crypto.getRandomValues(nonce); } catch { nonce.forEach((_, index) => { nonce[index] = Math.random() * 4294967296; }); }
    const session = Array.from(nonce, number => number.toString(16).padStart(8, '0')).join('');
    const embedded = root.parent !== root;
    try { root.history.scrollRestoration = 'manual'; } catch {}
    const blank = () => ({ version: 1, updatedAt: 0, activeTab: 'transcript', tabs: {}, mindmap: null });
    let state = blank();
    try { state = sanitize(JSON.parse(root.localStorage.getItem(storageKey))) || state; } catch {}
    let started = false, restoring = false, changed = false, touched = false, explicit = false;
    let nativeInitialized = !embedded, deferredWrite = false;
    const pendingFlushes = new Set();
    let restoreVersion = 0, persistTimer = 0, scrollFrame = 0, resizeFrame = 0, resumeTimer = 0;
    let activeTab = options.getActiveTab(), mindmap = null, localFailureShown = false;
    const cached = new Map();
    const clone = value => JSON.parse(JSON.stringify(value));
    // A sticky element's offset can change while it is pinned. This zero-height
    // flow marker keeps the header boundary independent of the current scroll.
    const origin = document.createElement('span');
    origin.setAttribute('aria-hidden', 'true');
    origin.style.cssText = 'display:block;height:0;width:0;pointer-events:none';
    $('.tabs')?.before(origin);
    const normalTabTop = () => origin.getBoundingClientRect().top + root.scrollY;
    const topInset = () => ($('.tabs')?.getBoundingClientRect().height || 0) + 12;
    const selectors = {
      transcript: '.reading-paragraph',
      shownotes: '.shownotes-copy.shownotes-plain,.shownotes-copy > div,.shownotes-copy p,.shownotes-copy li,.shownotes-copy h2,.shownotes-copy h3,.shownotes-copy h4,.shownotes-copy h5,.shownotes-copy h6,.shownotes-copy pre,.shownotes-copy blockquote',
      summary: '.summary-lead,.summary-line,.source-caveat',
      highlights: '.anki-card,.quote',
      keywords: '.keyword-atlas',
      mindmap: '.mindmap-wrap',
    };
    function content(name) {
      if (cached.has(name)) return cached.get(name);
      const panel = $('[data-panel="' + name + '"]');
      let elements = panel ? Array.from(panel.querySelectorAll(selectors[name] || '.panel-inner > *')) : [];
      if (!elements.length && panel) elements = Array.from(panel.querySelectorAll('.panel-inner > *'));
      const selected = new Set(elements);
      elements = elements.filter(element => {
        for (let parent = element.parentElement; parent && parent !== panel; parent = parent.parentElement) {
          if (selected.has(parent)) return false;
        }
        return true;
      });
      const seen = new Map(), values = [];
      const sourceSignature = name === 'transcript' && panel?.querySelector('.article-body')?.dataset.readingSignature;
      const anchors = elements.map(element => {
        const text = lexical(element.textContent || '');
        const sourceToken = element.dataset.readingToken;
        const id = token(sourceSignature) && token(sourceToken) ? sourceToken : fingerprint(text);
        const occurrence = seen.get(id) || 0;
        seen.set(id, occurrence + 1); values.push(text);
        const cue = Number(element.dataset.cueStart), endCue = Number(element.dataset.cueEnd);
        return { element, token: id, occurrence, ...(name === 'transcript' && integer(cue, 10000000) && integer(endCue, 10000000)
          ? { kind: 'cue', cue, endCue } : { kind: 'block' }) };
      });
      const result = { anchors, signature: token(sourceSignature) ? sourceSignature : fingerprint(values.join('')) };
      cached.set(name, result);
      return result;
    }
    function capture() {
      if (!started || restoring) return;
      const { anchors, signature } = content(activeTab), y = root.scrollY || 0, line = topInset();
      let position = { signature, anchor: { kind: 'top' }, offset: Math.min(y, 100000), ratio: 0 };
      if (y > normalTabTop() + 1) {
        const visible = anchors.find(anchor => anchor.element.getBoundingClientRect().bottom > line);
        if (visible) {
          const rect = visible.element.getBoundingClientRect(), offset = line - rect.top;
          const { element, ...anchor } = visible;
          position = { signature, anchor, offset: Math.max(-100000, Math.min(100000, offset)), ratio: Math.max(0, Math.min(1, offset / Math.max(1, rect.height))) };
        }
      }
      state.tabs[activeTab] = position;
      state.activeTab = activeTab;
      const map = mindmap?.capture();
      if (map && validMindmap(map)) state.mindmap = map;
    }
    function post(name, payload) {
      if (embedded) root.parent.postMessage({ [name]: { version: 1, key, session, ...payload } }, '*');
    }
    function saveLocal() {
      try { root.localStorage.setItem(storageKey, JSON.stringify(state)); }
      catch {
        if (!embedded && !localFailureShown) {
          localFailureShown = true;
          options.notice?.('Reading position could not be saved in this browser.');
        }
      }
    }
    function persist(requestId) {
      clearTimeout(persistTimer);
      capture();
      // A default first paint is not a new reading position. Wait for the
      // native record (including an explicit null) before publishing anything.
      if (!nativeInitialized) {
        if (requestId && pendingFlushes.size < 8) pendingFlushes.add(requestId);
        if (touched || explicit || changed) {
          state.updatedAt = Date.now();
          if (sanitize(state)) saveLocal();
          deferredWrite = true;
        }
        return;
      }
      state.updatedAt = Date.now();
      const valid = sanitize(state);
      if (!valid) return;
      state = valid;
      saveLocal();
      changed = false;
      post('linkReadPositionChanged', { state: clone(state), ...(requestId ? { requestId } : {}) });
    }
    function scheduleSave() {
      if (!started || restoring) return;
      changed = true;
      clearTimeout(persistTimer);
      persistTimer = root.setTimeout(persist, 250);
    }
    function hideResume() {
      clearTimeout(resumeTimer);
      const banner = $('[data-reading-resume]');
      if (banner) banner.hidden = true;
    }
    function showResume() {
      const banner = $('[data-reading-resume]');
      if (!banner) return;
      banner.hidden = false;
      clearTimeout(resumeTimer);
      resumeTimer = root.setTimeout(hideResume, 9000);
    }
    function instantScroll(top) {
      const html = document.documentElement, old = html.style.scrollBehavior;
      html.style.scrollBehavior = 'auto';
      root.scrollTo({ top: Math.max(0, top), left: 0, behavior: 'instant' });
      html.style.scrollBehavior = old;
    }
    function applyPosition(position, fallbackTop) {
      const data = content(activeTab);
      if (!position || !validPosition(position)) { instantScroll(fallbackTop); return false; }
      if (position.anchor.kind === 'top') {
        instantScroll(Math.min(Math.max(0, position.offset), normalTabTop()));
        return true;
      }
      const resolved = resolveAnchor(position, data.anchors, data.signature);
      if (!resolved) { instantScroll(0); return false; }
      const rect = resolved.anchor.element.getBoundingClientRect();
      const offset = resolved.within ? position.offset < 0
        ? Math.max(position.offset, -root.innerHeight / 2)
        : position.ratio * rect.height : 0;
      instantScroll(root.scrollY + rect.top - topInset() + offset);
      return true;
    }
    function restore(position, { announce = false, fallbackTop = 0 } = {}) {
      const version = ++restoreVersion;
      restoring = true;
      const apply = () => {
        if (version !== restoreVersion) return;
        const restored = applyPosition(position, fallbackTop);
        root.requestAnimationFrame(() => {
          if (version !== restoreVersion) return;
          restoring = false;
          capture();
          if (announce && restored) showResume();
        });
      };
      root.requestAnimationFrame(() => root.requestAnimationFrame(apply));
      // Bundled pages use local fonts; if they finish after the first layout,
      // anchor once more only while the user has not interacted.
      document.fonts?.ready.then(() => {
        if (version === restoreVersion && !touched && restoring) apply();
      });
    }
    function cancelRestore() {
      restoreVersion++;
      restoring = false;
    }
    function ownHash(hash) {
      const own = root.history.state?.linkReadTab;
      return own?.key === key && own?.tab === hash;
    }
    function updateHash(name) {
      root.history.replaceState({ ...(object(root.history.state) ? root.history.state : {}), linkReadTab: { key, tab: name } }, '', '#' + name);
    }
    function navigation() {
      explicit = true;
      cancelRestore();
      hideResume();
    }
    function beforeTab() {
      capture();
      cancelRestore();
      if (started) hideResume();
    }
    function afterTab(name, shouldRestore = true) {
      activeTab = name;
      state.activeTab = name;
      if (!started) return;
      if (shouldRestore) restore(state.tabs[name], { fallbackTop: Math.min(root.scrollY, normalTabTop()) });
      changed = true;
      clearTimeout(persistTimer);
      persistTimer = root.setTimeout(persist, 350);
    }
    function start(hasExplicitNavigation) {
      explicit = hasExplicitNavigation;
      if (!explicit) {
        const desired = options.tabNames.includes(state.activeTab) ? state.activeTab : 'transcript';
        const position = state.tabs[desired];
        options.activateTab(desired);
        started = true;
        restore(position, { announce: !!position });
      } else started = true;
      post('linkReadPositionReady', {});
    }
    function acceptRestore(value) {
      const incoming = value.state === null ? null : sanitize(value.state);
      if (value.state !== null && !incoming) return;
      nativeInitialized = true;
      if (incoming) {
        if (touched || explicit || changed) {
          // Late native responses may fill untouched tabs, never move the reader.
          for (const name of TABS) if (name !== activeTab && !state.tabs[name] && incoming.tabs[name]) state.tabs[name] = incoming.tabs[name];
          if (!state.mindmap && incoming.mindmap && activeTab !== 'mindmap') state.mindmap = incoming.mindmap;
        } else if (incoming.updatedAt >= state.updatedAt) {
          state = incoming;
          const desired = options.tabNames.includes(state.activeTab) ? state.activeTab : 'transcript';
          const position = state.tabs[desired];
          started = false;
          options.activateTab(desired);
          started = true;
          mindmap?.restore(state.mindmap);
          restore(position, { announce: !!position });
        }
      }
      const pending = [...pendingFlushes];
      pendingFlushes.clear();
      if (pending.length) pending.forEach(persist);
      else if (deferredWrite) persist();
      deferredWrite = false;
    }
    root.addEventListener('message', event => {
      if (!embedded || event.source !== root.parent) return;
      const incoming = event.data?.linkReadPositionRestore, flush = event.data?.linkReadPositionFlush;
      const value = incoming || flush;
      if (!object(value) || value.version !== 1 || value.key !== key || value.session !== session) return;
      if (incoming) acceptRestore(incoming);
      else if (typeof flush.requestId === 'string' && flush.requestId.length > 0 && flush.requestId.length <= 128) persist(flush.requestId);
    });
    root.addEventListener('scroll', () => {
      if (!started || restoring) return;
      if (!scrollFrame) scrollFrame = root.requestAnimationFrame(() => {
        scrollFrame = 0; capture(); scheduleSave();
      });
    }, { passive: true });
    root.addEventListener('resize', () => {
      if (!started || restoring || document.fullscreenElement || document.body.classList.contains('mindmap-fullscreen-open')) return;
      const position = state.tabs[activeTab];
      root.cancelAnimationFrame(resizeFrame);
      resizeFrame = root.requestAnimationFrame(() => restore(position));
    });
    // Pointer/keyboard intent wins over an in-flight restore, including native I/O.
    const activity = event => {
      if (event.type === 'keydown' && ['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) return;
      touched = true;
      cancelRestore();
    };
    document.addEventListener('pointerdown', activity, { passive: true, capture: true });
    document.addEventListener('wheel', activity, { passive: true, capture: true });
    document.addEventListener('keydown', activity, true);
    root.addEventListener('pagehide', () => { if (started) persist(); });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden' && started) persist(); });
    $('[data-reading-start-top]')?.addEventListener('click', () => {
      navigation();
      instantScroll(0);
      capture(); persist();
    });
    $('[data-reading-resume-dismiss]')?.addEventListener('click', hideResume);
    $('[data-reading-resume]')?.addEventListener('mouseenter', () => clearTimeout(resumeTimer));
    $('[data-reading-resume]')?.addEventListener('focusin', () => clearTimeout(resumeTimer));
    return {
      key, session, start, beforeTab, afterTab, ownHash, updateHash, navigation, capture,
      flush: persist,
      preserveLayout: () => { capture(); const saved = state.tabs[activeTab]; return () => restore(saved); },
      getState: () => clone(state),
      getMindmap: signature => state.mindmap?.signature === signature ? clone(state.mindmap) : null,
      registerMindmap: handlers => { mindmap = handlers; },
      setMindmap: (value, save = true) => {
        if (!validMindmap(value)) return;
        state.mindmap = clone(value);
        if (save) scheduleSave();
      },
    };
  }
  const api = { fingerprint, lexical, visibleCenter, sanitize, validMindmap, resolveAnchor, create };
  root.LinkReadReadingPosition = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
