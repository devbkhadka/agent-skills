/** Bound the actual JSON stdout, including pretty printing and trailing newline. */
const protectedKeys = new Set(['error', 'code', 'message', 'state', 'attached', 'bound', 'started', 'stopped', 'alreadyRunning', 'reattached', 'seq', 'offset', 'nextOffset', 'total', 'nextSeq', 'latestSeq', 'lastSeq', 'hasMore', 'dropped', 'index', 'frame', 'id', 'omitted', 'omittedCount', 'truncated', 'outputTruncated']);
export function serializeOutput(value, { pretty = false, maxBytes = 16384, since = 0 } = {}) {
  const encode = (v) => `${JSON.stringify(v, null, pretty ? 2 : undefined)}\n`;
  const original = encode(value);
  if (Buffer.byteLength(original) <= maxBytes) return original;
  const result = JSON.parse(JSON.stringify(value));
  // Log pages: `omitted` must count every matching hit not returned, including budget-trimmed ones.
  const hitCount = Array.isArray(result.hits) ? result.hits.length : 0;
  const baseOmitted = Number.isSafeInteger(result.omitted) ? result.omitted : 0;
  /** @param {any} out */
  const syncOmitted = (out) => {
    if (!Array.isArray(out.hits)) return out;
    const omitted = baseOmitted + hitCount - out.hits.length;
    if (omitted > 0) out.omitted = omitted; else delete out.omitted;
    return out;
  };
  result.outputTruncated = { originalBytes: Buffer.byteLength(original), budgetBytes: maxBytes, omittedItems: 0, shortenedValues: 0 };
  let previousLength = Infinity;
  for (;;) {
    const current = encode(result), currentLength = Buffer.byteLength(current);
    if (currentLength <= maxBytes) return current;
    if (currentLength >= previousLength) break;
    previousLength = currentLength;
    let largest;
    const scan = (obj, parentKey = '') => {
      if (!obj || typeof obj !== 'object') return;
      for (const [key, item] of Object.entries(obj)) {
        if (key === 'outputTruncated') continue;
        if (Array.isArray(item) && item.length > 1) {
          const weight = Buffer.byteLength(JSON.stringify(item));
          if (!largest || weight > largest.weight) largest = { obj, key, item, weight, array: true };
        }
        if (typeof item === 'string' && item.length > 96 && !['code', 'state'].includes(key)) {
          const weight = Buffer.byteLength(JSON.stringify(item));
          if (!largest || weight > largest.weight) largest = { obj, key, item, weight };
        }
        if (item && typeof item === 'object') scan(item, key);
        // Very wide objects (such as evaluated dictionaries) also need bounded field counts.
        if (item && !Array.isArray(item) && typeof item === 'object' && key !== 'error' && Object.keys(item).length > 8) {
          const removable = Object.keys(item).filter((k) => !protectedKeys.has(k));
          const weight = Buffer.byteLength(JSON.stringify(item));
          if (removable.length > 4 && (!largest || weight > largest.weight)) largest = { obj, key, item, weight, removable };
        }
      }
    };
    scan(result);
    if (!largest) break;
    const { obj, key, item } = largest;
    if (largest.array) {
      const count = Math.max(1, Math.floor(item.length / 2));
      result.outputTruncated.omittedItems += item.length - count;
      obj[key] = item.slice(0, count);
      if (obj === result && key === 'stack' && result.nextOffset !== undefined) {
        result.nextOffset = (obj[key].at(-1)?.index ?? result.offset ?? 0) + 1;
        result.hasMore = true;
      }
      if (obj === result && key === 'hits') {
        result.nextSeq = obj[key].at(-1)?.seq ?? since;
        result.hasMore = true;
        syncOmitted(result);
      }
    } else if (largest.removable) {
      const remove = largest.removable.slice(Math.floor(largest.removable.length / 2));
      for (const field of remove) delete item[field];
      result.outputTruncated.omittedItems += remove.length;
    } else {
      obj[key] = `${item.slice(0, Math.max(48, Math.floor(item.length / 2)))}…[truncated]`;
      result.outputTruncated.shortenedValues++;
    }
  }
  // Degenerate structures still return a useful, bounded outcome with explicit omissions.
  const fallback = { outputTruncated: result.outputTruncated };
  for (const [key, item] of Object.entries(result)) {
    if (protectedKeys.has(key) && key !== 'outputTruncated') fallback[key] = item;
  }
  if (Array.isArray(result.stack) && result.nextOffset !== undefined) { fallback.stack = []; fallback.nextOffset = result.offset ?? 0; fallback.hasMore = true; }
  if (Array.isArray(result.hits)) { fallback.hits = []; fallback.nextSeq = since; fallback.hasMore = true; syncOmitted(fallback); }
  if (fallback.error) fallback.error = { code: fallback.error.code, message: String(fallback.error.message).slice(0, 256) };
  fallback.outputTruncated.omittedItems++;
  if (Buffer.byteLength(encode(fallback)) <= maxBytes) return encode(fallback);
  const minimal = { outputTruncated: result.outputTruncated };
  const keys = ['attached', 'bound', 'started', 'stopped', 'alreadyRunning', 'reattached', 'state', 'offset', 'nextOffset', 'total', 'nextSeq', 'latestSeq', 'lastSeq', 'hasMore', 'dropped', 'omitted'];
  for (const key of keys) {
    const item = fallback[key];
    if (typeof item === 'boolean' || typeof item === 'number') minimal[key] = item;
    else if (typeof item === 'string') minimal[key] = item.slice(0, 16);
  }
  if (fallback.error) minimal.error = { code: String(fallback.error.code).slice(0, 32), message: String(fallback.error.message).slice(0, 48) };
  if (Array.isArray(fallback.hits)) minimal.hits = [];
  if (Array.isArray(fallback.stack)) minimal.stack = [];
  return encode(minimal);
}
