/**
 * Live Element Annotator
 *
 * Lets the user select real DOM elements in a headed browser, attach comments to them, and
 * submit. Blocks until the user presses Submit or closes the page, then returns every comment
 * as JSON. No screenshots are taken.
 *
 * Usage (single blocking command):
 *   playwright-cli --raw run-code --filename="$S/element-annotator.js"
 *
 * In the page:
 *   Alt+S (or the "Select" switch)  toggle selection mode
 *   hover + click                   pick an element and open its comment box
 *   Cmd/Ctrl+Enter, Esc             save / cancel the comment box
 *   numbered pin                    edit or delete a saved comment
 *   Submit                          finish and return the comments to the CLI
 *
 * Runs inside run-code's VM sandbox: only `page` and JS builtins are available (no setTimeout,
 * require, or process). The in-page overlay function must stay self-contained because it is
 * serialized into the page by addInitScript/evaluate.
 */
async page => {
  const overlay = (bridge) => {
    if (window.top !== window || window[`${bridge}_teardown`]) return;

    const MODE_KEY = `${bridge}:mode`;
    const readMode = () => {
      try { return sessionStorage.getItem(MODE_KEY) !== 'off'; } catch { return true; }
    };
    const writeMode = (on) => {
      try { sessionStorage.setItem(MODE_KEY, on ? 'on' : 'off'); } catch { /* storage blocked */ }
    };
    const call = (msg) => (typeof window[bridge] === 'function' ? window[bridge](msg) : Promise.resolve(null));

    // Hash routers ("#/settings") are routes; plain anchors ("#top") are not.
    const pageKey = (href) => {
      const u = new URL(href);
      const route = /^#[!/]/.test(u.hash) ? u.hash : '';
      return u.origin + u.pathname + u.search + route;
    };

    // ---------- Element description ----------
    const testIdOf = (el) => el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test') || el.getAttribute('data-cy');
    const isUnique = (sel) => { try { return document.querySelectorAll(sel).length === 1; } catch { return false; } };

    // Shortest unique CSS path, anchored on the nearest unique id or test id.
    const anchorOf = (node) => {
      if (node.id && isUnique(`#${CSS.escape(node.id)}`)) return `#${CSS.escape(node.id)}`;
      for (const attr of ['data-testid', 'data-test-id', 'data-test', 'data-cy']) {
        const value = node.getAttribute(attr);
        if (value && isUnique(`[${attr}="${CSS.escape(value)}"]`)) return `[${attr}="${CSS.escape(value)}"]`;
      }
      return null;
    };
    const cssSelector = (el) => {
      const segments = [];
      for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
        const anchor = anchorOf(node);
        if (anchor) return [anchor, ...segments].join(' > ');
        const tag = node.localName;
        const siblings = node.parentElement ? [...node.parentElement.children].filter((c) => c.localName === tag) : [node];
        segments.unshift(siblings.length > 1 ? `${tag}:nth-of-type(${siblings.indexOf(node) + 1})` : tag);
        if (isUnique(segments.join(' > '))) return segments.join(' > ');
      }
      return segments.join(' > ');
    };

    const shortText = (el) => {
      const raw = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('alt') || el.getAttribute('placeholder') || '').replace(/\s+/g, ' ').trim();
      return raw.length > 80 ? `${raw.slice(0, 77)}...` : raw;
    };

    // React dev builds: component name + JSX source. React <=18 exposes _debugSource;
    // React 19 only exposes _debugStack, so the first non-React frame is reported instead.
    const componentName = (type) => {
      if (!type || typeof type === 'string') return null;
      if (typeof type === 'function') return type.displayName || type.name || null;
      if (type.displayName) return type.displayName;
      if (type.render) return type.render.displayName || type.render.name || null;
      if (type.type) return componentName(type.type);
      return null;
    };
    const stackSource = (stack) => {
      const lines = String(stack || '').split('\n').slice(1);
      for (const line of lines) {
        if (/react-stack-top-frame|jsx-dev-runtime|node_modules/.test(line)) continue;
        const m = line.match(/\(?((?:https?|file|webpack[-\w]*):\/\/[^\s)]+?):(\d+):(\d+)\)?\s*$/);
        if (m) return `${m[1].replace(/\?[^:]*$/, '')}:${m[2]}:${m[3]}`;
      }
      return null;
    };
    const reactInfo = (el) => {
      const key = Object.keys(el).find((k) => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
      if (!key) return null;
      const host = el[key];
      const info = {};
      if (host._debugSource) info.source = `${host._debugSource.fileName}:${host._debugSource.lineNumber}`;
      else if (host._debugStack) info.source = stackSource(host._debugStack.stack);
      const stack = [];
      for (let f = host.return; f && stack.length < 5; f = f.return) {
        const name = componentName(f.type);
        if (name && stack[stack.length - 1] !== name) stack.push(name);
      }
      if (stack.length) {
        info.component = stack[0];
        info.componentStack = stack;
      }
      return info.component || info.source ? info : null;
    };

    const describe = (el) => {
      const r = el.getBoundingClientRect();
      const record = {
        selector: cssSelector(el),
        testId: testIdOf(el) || undefined,
        tag: el.localName,
        role: el.getAttribute('role') || undefined,
        text: shortText(el) || undefined,
        box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
        url: location.href,
      };
      const react = reactInfo(el);
      if (react) Object.assign(record, react);
      return record;
    };

    // ---------- Overlay (Shadow DOM, so page CSS cannot leak in) ----------
    const host = document.createElement('pw-element-annotator');
    host.setAttribute('style', 'all: initial !important; position: fixed !important; inset: 0 !important; pointer-events: none !important; z-index: 2147483647 !important;');
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        * { box-sizing: border-box; font: 13px/1.4 system-ui, -apple-system, sans-serif; }
        .hover { position: fixed; pointer-events: none; outline: 2px solid #2563eb; background: rgba(37,99,235,.12); border-radius: 2px; display: none; }
        .hover-label { position: absolute; left: 0; top: -20px; background: #2563eb; color: #fff; font-size: 11px; padding: 1px 5px; border-radius: 3px; white-space: nowrap; }
        .pin { position: fixed; pointer-events: auto; width: 22px; height: 22px; margin: -11px 0 0 -11px; border-radius: 50%; background: #dc2626; color: #fff; font-size: 11px; font-weight: 700; display: flex; align-items: center; justify-content: center; cursor: pointer; border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,.4); }
        .bar { position: fixed; right: 16px; bottom: 16px; pointer-events: auto; display: flex; gap: 10px; align-items: center; background: #111827; color: #f9fafb; padding: 8px 12px; border-radius: 10px; box-shadow: 0 4px 16px rgba(0,0,0,.35); }
        .bar label { display: flex; gap: 6px; align-items: center; cursor: pointer; user-select: none; }
        .switch { width: 32px; height: 18px; border-radius: 9px; background: #4b5563; position: relative; transition: background .15s; }
        .switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: #fff; transition: left .15s; }
        .on .switch { background: #2563eb; }
        .on .switch::after { left: 16px; }
        .hint { color: #9ca3af; font-size: 11px; }
        button { cursor: pointer; border: 0; border-radius: 6px; padding: 5px 10px; background: #374151; color: #f9fafb; }
        button.primary { background: #16a34a; }
        button.danger { background: #b91c1c; }
        .box { position: fixed; pointer-events: auto; width: 280px; background: #fff; color: #111827; border-radius: 8px; padding: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.3); display: none; }
        .box .target { font-size: 11px; color: #6b7280; margin-bottom: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .box textarea { width: 100%; min-height: 70px; resize: vertical; border: 1px solid #d1d5db; border-radius: 6px; padding: 6px; color: #111827; background: #fff; }
        .box .actions { display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px; }
      </style>
      <div class="hover"><span class="hover-label"></span></div>
      <div class="pins"></div>
      <div class="box">
        <div class="target"></div>
        <textarea placeholder="Comment on this element (Cmd/Ctrl+Enter to save)"></textarea>
        <div class="actions">
          <button class="danger" data-act="delete">Delete</button>
          <button data-act="cancel">Cancel</button>
          <button class="primary" data-act="save">Save</button>
        </div>
      </div>
      <div class="bar">
        <label title="Toggle selection mode (Alt+S)"><span class="switch"></span><span>Select</span><span class="hint">Alt+S</span></label>
        <span class="count">0 comments</span>
        <button class="primary" data-act="submit">Submit</button>
      </div>`;
    const $ = (sel) => root.querySelector(sel);
    const hoverEl = $('.hover');
    const hoverLabel = $('.hover-label');
    const pinsEl = $('.pins');
    const boxEl = $('.box');
    const boxTarget = $('.box .target');
    const textarea = $('.box textarea');
    const barLabel = $('.bar label');
    const countEl = $('.count');

    let selecting = readMode();
    let comments = [];
    let draft = null; // { element, record } for a new comment, or { id } when editing
    let hovered = null;
    let rafPending = false;
    const elementCache = new Map();

    const fromOverlay = (e) => e.composedPath().includes(host);
    const label = (el) => `${el.localName}${el.id ? `#${el.id}` : ''}${testIdOf(el) ? `[data-testid=${testIdOf(el)}]` : ''}`;

    const placeAt = (node, r) => Object.assign(node.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });

    const resolve = (c) => {
      const cached = elementCache.get(c.id);
      if (cached && cached.isConnected) return cached;
      let el = null;
      try { el = document.querySelector(c.selector); } catch { /* invalid selector */ }
      if (el) elementCache.set(c.id, el); else elementCache.delete(c.id);
      return el;
    };

    const render = () => {
      rafPending = false;
      barLabel.parentElement.classList.toggle('on', selecting);
      countEl.textContent = `${comments.length} comment${comments.length === 1 ? '' : 's'}`;
      if (selecting && hovered && hovered.isConnected && !draft) {
        placeAt(hoverEl, hovered.getBoundingClientRect());
        hoverLabel.textContent = label(hovered);
        hoverEl.style.display = 'block';
      } else if (!(draft && draft.element)) {
        hoverEl.style.display = 'none';
      }
      const here = pageKey(location.href);
      const visible = comments.filter((c) => pageKey(c.url) === here);
      while (pinsEl.children.length > visible.length) pinsEl.lastChild.remove();
      visible.forEach((c, i) => {
        let pin = pinsEl.children[i];
        if (!pin) {
          pin = document.createElement('div');
          pin.className = 'pin';
          pinsEl.appendChild(pin);
        }
        pin.textContent = String(c.id);
        pin.dataset.id = String(c.id);
        pin.title = c.note;
        const el = resolve(c);
        if (!el) { pin.style.display = 'none'; return; }
        const r = el.getBoundingClientRect();
        Object.assign(pin.style, { display: 'flex', left: `${Math.max(11, r.left)}px`, top: `${Math.max(11, r.top)}px` });
      });
    };
    const schedule = () => {
      if (!rafPending) { rafPending = true; requestAnimationFrame(render); }
    };

    const setComments = (list) => {
      if (!Array.isArray(list)) return;
      comments = list;
      window.__annotations = list.map((c) => ({ ...c }));
      schedule();
    };

    const openBox = (anchorRect, title, note, editing) => {
      boxTarget.textContent = title;
      textarea.value = note;
      $('[data-act="delete"]').style.display = editing ? '' : 'none';
      const left = Math.min(Math.max(8, anchorRect.left), innerWidth - 288);
      const below = anchorRect.bottom + 8;
      const top = below + 150 < innerHeight ? below : Math.max(8, anchorRect.top - 158);
      Object.assign(boxEl.style, { display: 'block', left: `${left}px`, top: `${top}px` });
      textarea.focus();
      schedule();
    };
    const closeBox = () => {
      draft = null;
      boxEl.style.display = 'none';
      hoverEl.style.display = 'none';
      schedule();
    };

    const saveBox = async () => {
      const note = textarea.value.trim();
      const current = draft;
      if (!current) return;
      if (!note) { textarea.focus(); return; }
      closeBox();
      if (current.id) setComments(await call({ type: 'update', id: current.id, note }));
      else setComments(await call({ type: 'save', comment: { ...current.record, note } }));
    };

    const setSelecting = (on) => {
      selecting = on;
      writeMode(on);
      if (!on) hovered = null;
      schedule();
    };

    // ---------- Event handling (capture phase; ignores events from the overlay itself) ----------
    const onMove = (e) => {
      if (!selecting || draft || fromOverlay(e)) return;
      const el = e.target instanceof Element ? e.target : null;
      if (el !== hovered) { hovered = el; schedule(); }
    };
    const swallow = (e) => {
      if (!selecting || fromOverlay(e)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
    };
    const onClick = (e) => {
      if (!selecting || fromOverlay(e)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (draft || !(e.target instanceof Element)) return;
      const element = e.target;
      draft = { element, record: describe(element) };
      placeAt(hoverEl, element.getBoundingClientRect());
      hoverLabel.textContent = label(element);
      hoverEl.style.display = 'block';
      openBox(element.getBoundingClientRect(), draft.record.selector, '', false);
    };
    const onKey = (e) => {
      if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyS') {
        e.preventDefault();
        e.stopImmediatePropagation();
        setSelecting(!selecting);
      }
    };
    const blockedEvents = ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'dblclick', 'auxclick', 'contextmenu'];
    window.addEventListener('mousemove', onMove, true);
    window.addEventListener('click', onClick, true);
    window.addEventListener('keydown', onKey, true);
    blockedEvents.forEach((t) => window.addEventListener(t, swallow, true));
    window.addEventListener('scroll', schedule, { capture: true, passive: true });
    window.addEventListener('resize', schedule);

    barLabel.addEventListener('click', (e) => { e.preventDefault(); setSelecting(!selecting); });
    $('[data-act="submit"]').addEventListener('click', async () => {
      closeBox();
      await call({ type: 'submit' });
    });
    $('[data-act="save"]').addEventListener('click', saveBox);
    $('[data-act="cancel"]').addEventListener('click', closeBox);
    $('[data-act="delete"]').addEventListener('click', async () => {
      const current = draft;
      closeBox();
      if (current && current.id) setComments(await call({ type: 'delete', id: current.id }));
    });
    textarea.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveBox(); }
      if (e.key === 'Escape') { e.preventDefault(); closeBox(); }
    });
    pinsEl.addEventListener('click', (e) => {
      const pin = e.target.closest('.pin');
      if (!pin) return;
      const c = comments.find((x) => String(x.id) === pin.dataset.id);
      if (!c) return;
      draft = { id: c.id };
      openBox(pin.getBoundingClientRect(), `#${c.id} ${c.selector}`, c.note, true);
    });

    // Keep the overlay attached across SPA re-renders and route changes.
    const observer = new MutationObserver(() => {
      if (!host.isConnected) document.documentElement.appendChild(host);
      schedule();
    });
    const tick = setInterval(schedule, 500);

    const mount = () => {
      document.documentElement.appendChild(host);
      observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
      call({ type: 'list' }).then(setComments);
      schedule();
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
    else mount();

    window[`${bridge}_teardown`] = (message) => {
      window.removeEventListener('mousemove', onMove, true);
      window.removeEventListener('click', onClick, true);
      window.removeEventListener('keydown', onKey, true);
      blockedEvents.forEach((t) => window.removeEventListener(t, swallow, true));
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
      observer.disconnect();
      clearInterval(tick);
      try { sessionStorage.removeItem(MODE_KEY); } catch { /* storage blocked */ }
      root.innerHTML = '<div style="position:fixed;right:16px;bottom:16px;background:#16a34a;color:#fff;padding:8px 12px;border-radius:10px;font:13px system-ui,sans-serif"></div>';
      root.firstChild.textContent = message;
      setTimeout(() => host.remove(), 2500);
      delete window[`${bridge}_teardown`];
    };
  };

  // ---------- Node side: comment store, bridge, and blocking wait ----------
  const bridge = `__pwAnnotator_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const comments = [];
  let nextId = 1;
  let finish;
  const finished = new Promise((resolve) => { finish = resolve; });

  // Playwright's own locator generator (getByRole/getByTestId/...) for each saved element.
  const addLocator = async (comment) => {
    try {
      const normalized = await Promise.race([
        page.locator(comment.selector).first().normalize(),
        page.waitForTimeout(3000).then(() => null),
      ]);
      if (normalized) comment.locator = normalized.toString();
    } catch { /* element gone or page navigated; the CSS selector is still recorded */ }
  };

  const ordered = (c) => {
    const { id, note, selector, locator, testId, tag, role, text, box, url, component, source, componentStack } = c;
    return { id, note, selector, locator, testId, tag, role, text, box, url, component, source, componentStack };
  };

  const binding = await page.exposeBinding(bridge, async (_source, msg) => {
    switch (msg && msg.type) {
      case 'list':
        return comments.map(ordered);
      case 'save': {
        const comment = { ...msg.comment, id: nextId++, note: String(msg.comment.note).slice(0, 4000) };
        comments.push(comment);
        await addLocator(comment);
        return comments.map(ordered);
      }
      case 'update': {
        const comment = comments.find((c) => c.id === msg.id);
        if (comment) comment.note = String(msg.note).slice(0, 4000);
        return comments.map(ordered);
      }
      case 'delete': {
        const index = comments.findIndex((c) => c.id === msg.id);
        if (index >= 0) comments.splice(index, 1);
        return comments.map(ordered);
      }
      case 'submit':
        finish('submitted');
        return comments.map(ordered);
      default:
        return null;
    }
  });
  const initScript = await page.addInitScript(overlay, bridge);
  await page.evaluate(overlay, bridge).catch(() => { /* page mid-navigation; the init script covers it */ });

  const onClose = () => finish('closed');
  page.once('close', onClose);
  const status = await finished;
  page.off('close', onClose);

  await initScript.dispose().catch(() => {});
  if (!page.isClosed()) {
    const message = comments.length ? `Submitted ${comments.length} comment${comments.length === 1 ? '' : 's'}` : 'No annotations';
    await page.evaluate(([b, m]) => window[`${b}_teardown`] && window[`${b}_teardown`](m), [bridge, message]).catch(() => {});
  }
  await binding.dispose().catch(() => {});

  if (!comments.length) return { status, message: 'No annotations', annotations: [] };
  return { status, count: comments.length, annotations: comments.map(ordered) };
}
