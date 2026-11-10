/**
 * React Fiber & DOM Coordinate Probing Script
 * 
 * Evaluates DOM elements at specific viewport coordinates (x, y) or by element ref,
 * extracting the React component hierarchy, debug source file locations, owner context,
 * and DOM/accessibility attributes with minimal token output.
 *
 * Compatible with React 16-19 (Vite, Webpack, Next.js, CRA) and provides fallbacks
 * for Vue, Svelte, and standard DOM elements.
 * 
 * Usage in playwright-cli:
 *   playwright-cli eval "($(cat scripts/probe-fiber.js))({ x: 250, y: 180 })"
 */

((params = {}) => {
  const targetX = params.x || 0;
  const targetY = params.y || 0;
  const targetW = params.width || 0;
  const targetH = params.height || 0;

  const centerX = targetX + (targetW / 2);
  const centerY = targetY + (targetH / 2);

  const el = document.elementFromPoint(centerX, centerY) || document.body;
  if (!el) {
    return { error: `No element found at point (${centerX}, ${centerY})` };
  }

  // Helper to extract clean component name
  const getComponentName = (type) => {
    if (!type) return null;
    if (typeof type === 'string') return type;
    if (typeof type === 'function') return type.displayName || type.name || null;
    if (typeof type === 'object') {
      if (type.displayName) return type.displayName;
      if (type.render) return type.render.displayName || type.render.name || 'ForwardRef';
      if (type.$$typeof) {
        const str = String(type.$$typeof);
        if (str.includes('react.memo')) return getComponentName(type.type) ? `Memo(${getComponentName(type.type)})` : 'Memo';
        if (str.includes('react.provider')) return 'ContextProvider';
        if (str.includes('react.context')) return 'ContextConsumer';
      }
    }
    return null;
  };

  // 1. Check for React Fiber
  const fiberKey = Object.keys(el).find(k =>
    k.startsWith('__reactFiber$') ||
    k.startsWith('__reactInternalInstance$') ||
    k.startsWith('__reactContainer$')
  );

  const reactHierarchy = [];
  if (fiberKey && el[fiberKey]) {
    let fiber = el[fiberKey];
    while (fiber && reactHierarchy.length < 10) {
      const name = getComponentName(fiber.type);
      const source = fiber._debugSource
        ? `${fiber._debugSource.fileName}:${fiber._debugSource.lineNumber}`
        : (fiber._debugInfo && fiber._debugInfo.length ? fiber._debugInfo[0].name : null);
      
      const owner = fiber._debugOwner ? getComponentName(fiber._debugOwner.type) : null;

      // Filter out raw HTML tags and wrapper noise
      if (name && !name.startsWith('_') && !['div', 'span', 'p', 'a', 'button', 'input', 'ul', 'li', 'section', 'main', 'header', 'footer', 'svg', 'path'].includes(name.toLowerCase())) {
        const entry = { name };
        if (source) entry.source = source;
        if (owner && owner !== name) entry.owner = owner;
        
        // Avoid duplicate adjacent entries
        if (reactHierarchy.length === 0 || reactHierarchy[reactHierarchy.length - 1].name !== name) {
          reactHierarchy.push(entry);
        }
      }
      fiber = fiber.return;
    }
  }

  // 2. Check for Vue component instance
  let vueComponent = null;
  if (el.__vueParentComponent) {
    const vComp = el.__vueParentComponent;
    vueComponent = {
      name: vComp.type?.__name || vComp.type?.name || 'AnonymousVueComponent',
      file: vComp.type?.__file || null
    };
  }

  // 3. Extract DOM & Accessibility context
  const getSemanticSelector = (element) => {
    if (element.id) return `#${element.id}`;
    const testId = element.getAttribute('data-testid') || element.getAttribute('data-test') || element.getAttribute('data-cy');
    if (testId) return `[data-testid="${testId}"]`;
    const role = element.getAttribute('role');
    const ariaLabel = element.getAttribute('aria-label');
    if (role && ariaLabel) return `[role="${role}"][aria-label="${ariaLabel}"]`;
    if (element.className && typeof element.className === 'string') {
      const classes = element.className.trim().split(/\s+/).slice(0, 2).join('.');
      if (classes) return `${element.tagName.toLowerCase()}.${classes}`;
    }
    return element.tagName.toLowerCase();
  };

  return {
    point: { x: Math.round(centerX), y: Math.round(centerY) },
    element: {
      tagName: el.tagName.toLowerCase(),
      id: el.id || undefined,
      testId: el.getAttribute('data-testid') || el.getAttribute('data-cy') || undefined,
      role: el.getAttribute('role') || undefined,
      ariaLabel: el.getAttribute('aria-label') || undefined,
      selector: getSemanticSelector(el),
      innerTextSnippet: el.innerText ? el.innerText.trim().slice(0, 60) : undefined
    },
    framework: reactHierarchy.length > 0 ? 'react' : (vueComponent ? 'vue' : 'dom'),
    reactHierarchy: reactHierarchy.length > 0 ? reactHierarchy : undefined,
    vueComponent: vueComponent || undefined
  };
})
