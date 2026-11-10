/**
 * Presentation helpers: CDP RemoteObject -> compact JSON preview, source excerpts, display paths.
 *
 * Previews are meant for an LLM reading terminal output: plain JSON values where possible,
 * strings truncated, objects capped, non-JSON primitives rendered as their description.
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const MAX_STRING = 200;
/** Entries shown per array/object preview; the rest is summarized as `"…": "+N more"`. */
export const MAX_PROPS = 10;
const OVERFLOW_KEY = '…';

/**
 * @typedef {{ type: string, subtype?: string, className?: string, value?: unknown, unserializableValue?: string,
 *   description?: string, objectId?: string, preview?: ObjectPreview }} RemoteObject
 * @typedef {{ type: string, subtype?: string, description?: string, overflow: boolean,
 *   properties: PropertyPreview[], entries?: { key?: ObjectPreview, value: ObjectPreview }[] }} ObjectPreview
 * @typedef {{ name: string, type: string, value?: string, subtype?: string, valuePreview?: ObjectPreview }} PropertyPreview
 * @typedef {{ name: string, value?: RemoteObject, get?: RemoteObject, set?: RemoteObject, enumerable?: boolean, isOwn?: boolean }} PropertyDescriptor
 * @typedef {(objectId: string) => Promise<PropertyDescriptor[]>} GetProperties
 */

/**
 * @param {string} text
 * @param {number} [max]
 */
export function truncate(text, max = MAX_STRING) {
  return text.length > max ? `${text.slice(0, max)}…(+${text.length - max} chars)` : text;
}

/** Subtypes whose built-in preview is more useful than walking own properties. */
const PREVIEW_ONLY_SUBTYPES = new Set(['map', 'set', 'weakmap', 'weakset', 'iterator', 'generator', 'promise', 'proxy', 'arraybuffer', 'dataview', 'wasmvalue']);

/**
 * Preview a RemoteObject. depth 0 = description only, 1 = V8's one-level preview, >= 2 walks own
 * enumerable properties via `getProperties` (capped at MAX_PROPS) and recurses with depth - 1.
 * @param {RemoteObject | undefined} obj
 * @param {number} depth
 * @param {GetProperties} [getProperties]
 * @returns {Promise<unknown>}
 */
export async function previewValue(obj, depth, getProperties) {
  const primitive = previewPrimitive(obj);
  if (primitive.done) return primitive.value;
  const o = /** @type {RemoteObject} */ (obj);
  if (o.subtype === 'error') return truncate(firstLines(o.description ?? 'Error', 2));
  if (o.subtype === 'date' || o.subtype === 'regexp') return o.description;
  if (depth <= 0 || !o.objectId) return o.description ?? o.type;
  if (depth === 1 || !getProperties || PREVIEW_ONLY_SUBTYPES.has(o.subtype ?? '')) {
    return o.preview ? fromObjectPreview(o.preview) : o.description;
  }
  let props;
  try {
    props = await getProperties(o.objectId);
  } catch {
    return o.preview ? fromObjectPreview(o.preview) : o.description;
  }
  const own = props.filter((p) => p.enumerable !== false && p.isOwn !== false && !isArrayLength(o, p));
  const isArray = o.subtype === 'array' || o.subtype === 'typedarray';
  /** @type {Record<string, unknown> | unknown[]} */
  const out = isArray ? [] : {};
  if (!isArray && o.className && o.className !== 'Object') /** @type {Record<string, unknown>} */ (out).$class = o.className;
  for (const p of own.slice(0, MAX_PROPS)) {
    const value = p.value ? await previewValue(p.value, depth - 1, getProperties) : p.get ? '(getter)' : undefined;
    if (Array.isArray(out)) out.push(value);
    else out[p.name] = value;
  }
  if (own.length > MAX_PROPS) {
    if (Array.isArray(out)) out.push(`${OVERFLOW_KEY}+${own.length - MAX_PROPS} more`);
    else out[OVERFLOW_KEY] = `+${own.length - MAX_PROPS} more`;
  }
  return out;
}

/**
 * @param {RemoteObject} obj
 * @param {PropertyDescriptor} prop
 */
function isArrayLength(obj, prop) {
  return (obj.subtype === 'array' || obj.subtype === 'typedarray') && prop.name === 'length';
}

/**
 * @param {RemoteObject | undefined} obj
 * @returns {{ done: true, value: unknown } | { done: false }}
 */
function previewPrimitive(obj) {
  if (!obj) return { done: true, value: 'undefined' };
  switch (obj.type) {
    case 'undefined':
      return { done: true, value: 'undefined' };
    case 'string':
      return { done: true, value: truncate(String(obj.value)) };
    case 'number':
    case 'boolean':
      return { done: true, value: obj.unserializableValue ?? obj.value };
    case 'bigint':
    case 'symbol':
      return { done: true, value: obj.unserializableValue ?? obj.description };
    case 'function':
      return { done: true, value: `ƒ ${truncate(firstLines(obj.description ?? 'anonymous', 1), 120)}` };
    case 'object':
      if (obj.subtype === 'null') return { done: true, value: null };
      return { done: false };
    default:
      return { done: true, value: obj.description ?? String(obj.value) };
  }
}

/**
 * V8 ObjectPreview -> plain JSON.
 * @param {ObjectPreview} preview
 * @returns {unknown}
 */
export function fromObjectPreview(preview) {
  if (preview.subtype === 'error') return truncate(firstLines(preview.description ?? 'Error', 2));
  if (preview.subtype === 'date' || preview.subtype === 'regexp') return preview.description;
  if (preview.entries) {
    const entries = preview.entries
      .slice(0, MAX_PROPS)
      .map((e) => (e.key ? `${previewText(e.key)} => ${previewText(e.value)}` : previewText(e.value)));
    const more = overflowText(preview, preview.entries.length);
    if (more) entries.push(`${OVERFLOW_KEY}${more}`);
    return { $class: preview.description ?? preview.subtype, entries };
  }
  if (preview.subtype === 'array' || preview.subtype === 'typedarray') {
    const items = preview.properties.slice(0, MAX_PROPS).map(fromPropertyPreview);
    const more = overflowText(preview, preview.properties.length);
    if (more) items.push(`${OVERFLOW_KEY}${more}`);
    return items;
  }
  /** @type {Record<string, unknown>} */
  const out = {};
  if (preview.description && preview.description !== 'Object') out.$class = preview.description;
  for (const p of preview.properties.slice(0, MAX_PROPS)) out[p.name] = fromPropertyPreview(p);
  const more = overflowText(preview, preview.properties.length);
  if (more) out[OVERFLOW_KEY] = more;
  return out;
}

/**
 * `+N more` for a preview with `shown` items of which MAX_PROPS are kept; the total comes from the
 * `Array(N)` / `Map(N)` description when the runtime truncated the preview itself. Null when complete.
 * @param {ObjectPreview} preview
 * @param {number} shown
 */
function overflowText(preview, shown) {
  const total = Number(/\((\d+)\)$/.exec(preview.description ?? '')?.[1] ?? NaN);
  const kept = Math.min(shown, MAX_PROPS);
  if (Number.isFinite(total) && total > kept) return `+${total - kept} more`;
  if (shown > kept) return `+${shown - kept}${preview.overflow ? '+' : ''} more`;
  return preview.overflow ? '+more' : null;
}

/** @param {PropertyPreview} p */
function fromPropertyPreview(p) {
  if (p.valuePreview) return fromObjectPreview(p.valuePreview);
  switch (p.type) {
    case 'string':
      return truncate(p.value ?? '');
    case 'number': {
      const n = Number(p.value);
      return Number.isFinite(n) ? n : p.value;
    }
    case 'boolean':
      return p.value === 'true';
    case 'object':
      return p.subtype === 'null' ? null : p.value;
    case 'function':
      return `ƒ ${p.value ?? ''}`.trim();
    default:
      return p.value;
  }
}

/** @param {ObjectPreview} preview */
function previewText(preview) {
  const value = fromObjectPreview(preview);
  if (preview.type !== 'object') return preview.description ?? String(value);
  return JSON.stringify(value);
}

/**
 * @param {string} text
 * @param {number} count
 */
function firstLines(text, count) {
  return text.split('\n').slice(0, count).join('\n');
}

/**
 * Numbered excerpt around a 0-based line, marking it with `>`.
 * @param {string} text
 * @param {number} line0
 * @param {number} [radius]
 * @returns {string[]}
 */
export function sourceExcerpt(text, line0, radius = 2) {
  const lines = text.split(/\r?\n/);
  const from = Math.max(0, line0 - radius);
  const to = Math.min(lines.length - 1, line0 + radius);
  const width = String(to + 1).length;
  const out = [];
  for (let i = from; i <= to; i++) {
    out.push(`${i === line0 ? '>' : ' '} ${String(i + 1).padStart(width)} | ${truncate(lines[i], 160)}`);
  }
  return out;
}

/**
 * Convert a script URL or source identifier to a filesystem path when it is one.
 * @param {string} urlOrPath
 */
export function toFsPath(urlOrPath) {
  if (/^file:\/\//i.test(urlOrPath)) {
    try {
      return fileURLToPath(urlOrPath);
    } catch {
      return urlOrPath;
    }
  }
  return urlOrPath;
}

/**
 * Path shown to the user: relative to cwd when inside it, else absolute / URL as-is.
 * @param {string} urlOrPath
 * @param {string} cwd
 */
export function displayPath(urlOrPath, cwd) {
  const p = toFsPath(urlOrPath);
  if (!path.isAbsolute(p)) return p;
  const rel = path.relative(cwd, p);
  return rel && !rel.startsWith('..') && !path.isAbsolute(rel) ? rel : p;
}
