/**
 * Source-map core for the js-runtime-debugger daemon.
 *
 * Translates breakpoints given in ORIGINAL source coordinates
 * (e.g. `apps/client-api/src/foo.ts:42`) into GENERATED script coordinates,
 * and paused call frames back into original file:line.
 *
 * Pure and zero-dependency: no I/O, no network, no module-level mutable state.
 * Fetching the script / map text is the caller's job.
 *
 * All lines and columns in this module are 0-based.
 *
 * Usage:
 *   import { SourceMapIndex, parseSourceMappingURL, decodeDataUrl } from './sourcemap.mjs';
 *   const index = SourceMapIndex.fromJSON(mapText, { mapUrl, scriptUrl });
 *   const src = index.findSource('src/foo.ts');
 *   const locations = index.originalToGenerated(src, 41);
 */

import { fileURLToPath } from 'node:url';

/** Raised for any malformed source map or invalid argument. */
export class SourceMapError extends Error {
  /**
   * @param {string} message
   * @param {{ cause?: unknown }} [options]
   */
  constructor(message, options) {
    super(message, options);
    this.name = 'SourceMapError';
    this.code = 'ESOURCEMAP';
  }
}

const BASE64_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const BASE64_LOOKUP = (() => {
  const table = new Int8Array(128).fill(-1);
  for (let i = 0; i < BASE64_CHARS.length; i++) table[BASE64_CHARS.charCodeAt(i)] = i;
  return table;
})();
/** 2^(5k) for k = 0..6; VLQ values are limited to 7 digits (35 bits). */
const VLQ_POW = [1, 32, 1024, 32768, 1048576, 33554432, 1073741824];
const MAX_INT32 = 2147483647;
const COMMA = 44;
const SEMICOLON = 59;

/** Flat segment layout: [genLine, genCol, srcIdx, origLine, origCol, nameIdx]; -1 when absent. */
const STRIDE = 6;
const F_GEN_LINE = 0;
const F_GEN_COL = 1;
const F_SRC = 2;
const F_ORIG_LINE = 3;
const F_ORIG_COL = 4;
const F_NAME = 5;

/** How many original lines forward `originalToGenerated` may snap. */
const SNAP_LINES = 5;

// ---------------------------------------------------------------------------
// VLQ decoding
// ---------------------------------------------------------------------------

/** Growable Int32Array holding segments in the flat STRIDE layout. */
class SegmentBuffer {
  /** @param {number} initialSegments */
  constructor(initialSegments) {
    this.data = new Int32Array(Math.max(256, initialSegments) * STRIDE);
    this.count = 0;
  }

  /**
   * @param {number} genLine @param {number} genCol @param {number} src
   * @param {number} origLine @param {number} origCol @param {number} name
   */
  push(genLine, genCol, src, origLine, origCol, name) {
    let offset = this.count * STRIDE;
    if (offset + STRIDE > this.data.length) {
      const grown = new Int32Array(this.data.length * 2);
      grown.set(this.data);
      this.data = grown;
    }
    const d = this.data;
    d[offset++] = genLine;
    d[offset++] = genCol;
    d[offset++] = src;
    d[offset++] = origLine;
    d[offset++] = origCol;
    d[offset] = name;
    this.count++;
  }
}

/**
 * Decode a v3 `mappings` string, appending absolute segments to `out`.
 *
 * @param {unknown} mappings
 * @param {SegmentBuffer} out
 * @param {{ sourcesLength: number, namesLength: number, lineOffset: number, columnOffset: number, sourceMap?: Int32Array | null, nameBase: number }} ctx
 * @returns {number} number of generated lines spanned by the mappings
 */
function decodeInto(mappings, out, ctx) {
  if (typeof mappings !== 'string') {
    throw new SourceMapError('Invalid source map: "mappings" must be a string');
  }
  const len = mappings.length;
  const { sourcesLength, namesLength, lineOffset, columnOffset, sourceMap, nameBase } = ctx;
  let genLine = 0;
  let genCol = 0;
  let src = 0;
  let origLine = 0;
  let origCol = 0;
  let name = 0;
  let i = 0;
  const fields = [0, 0, 0, 0, 0];

  while (i < len) {
    const c = mappings.charCodeAt(i);
    if (c === SEMICOLON) {
      genLine++;
      genCol = 0;
      i++;
      continue;
    }
    if (c === COMMA) {
      i++;
      continue;
    }

    const segmentStart = i;
    let fieldCount = 0;
    while (i < len) {
      const next = mappings.charCodeAt(i);
      if (next === COMMA || next === SEMICOLON) break;
      if (fieldCount === 5) {
        throw new SourceMapError(`Invalid mappings: segment at offset ${segmentStart} has more than 5 fields`);
      }
      let value = 0;
      let digitIndex = 0;
      let continuation;
      do {
        if (i >= len) {
          throw new SourceMapError(`Invalid mappings: unterminated VLQ value at offset ${i}`);
        }
        const code = mappings.charCodeAt(i++);
        const digit = code < 128 ? BASE64_LOOKUP[code] : -1;
        if (digit === -1) {
          throw new SourceMapError(
            `Invalid mappings: unexpected character ${JSON.stringify(mappings[i - 1])} at offset ${i - 1}`,
          );
        }
        if (digitIndex >= VLQ_POW.length) {
          throw new SourceMapError(`Invalid mappings: VLQ value too large at offset ${i - 1}`);
        }
        continuation = digit & 32;
        value += (digit & 31) * VLQ_POW[digitIndex++];
      } while (continuation);
      const odd = value % 2;
      const magnitude = (value - odd) / 2;
      if (magnitude > MAX_INT32) {
        throw new SourceMapError(`Invalid mappings: VLQ value out of 32-bit range at offset ${segmentStart}`);
      }
      fields[fieldCount++] = odd ? -magnitude : magnitude;
    }

    if (fieldCount !== 1 && fieldCount !== 4 && fieldCount !== 5) {
      throw new SourceMapError(
        `Invalid mappings: segment at offset ${segmentStart} has ${fieldCount} fields (expected 1, 4 or 5)`,
      );
    }
    genCol += fields[0];
    if (genCol < 0) {
      throw new SourceMapError(`Invalid mappings: negative generated column at offset ${segmentStart}`);
    }
    let outSrc = -1;
    let outOrigLine = -1;
    let outOrigCol = -1;
    let outName = -1;
    if (fieldCount >= 4) {
      src += fields[1];
      origLine += fields[2];
      origCol += fields[3];
      if (src < 0 || src >= sourcesLength) {
        throw new SourceMapError(`Invalid mappings: source index ${src} out of range at offset ${segmentStart}`);
      }
      if (origLine < 0 || origCol < 0) {
        throw new SourceMapError(`Invalid mappings: negative original position at offset ${segmentStart}`);
      }
      outSrc = sourceMap ? sourceMap[src] : src;
      outOrigLine = origLine;
      outOrigCol = origCol;
      if (fieldCount === 5) {
        name += fields[4];
        if (name < 0 || name >= namesLength) {
          throw new SourceMapError(`Invalid mappings: name index ${name} out of range at offset ${segmentStart}`);
        }
        outName = nameBase + name;
      }
    }
    out.push(
      lineOffset + genLine,
      genLine === 0 ? columnOffset + genCol : genCol,
      outSrc,
      outOrigLine,
      outOrigCol,
      outName,
    );
  }
  return genLine + 1;
}

/**
 * Decode a Base64 VLQ `mappings` string (source map v3).
 *
 * @param {string} mappingsString
 * @returns {number[][][]} one array per generated line of absolute segments
 *   `[genCol]` or `[genCol, srcIdx, origLine, origCol]` or `[genCol, srcIdx, origLine, origCol, nameIdx]`
 */
export function decodeMappings(mappingsString) {
  const buffer = new SegmentBuffer(typeof mappingsString === 'string' ? mappingsString.length >> 3 : 0);
  const lineCount = decodeInto(mappingsString, buffer, {
    sourcesLength: Infinity,
    namesLength: Infinity,
    lineOffset: 0,
    columnOffset: 0,
    sourceMap: null,
    nameBase: 0,
  });
  /** @type {number[][][]} */
  const lines = Array.from({ length: lineCount }, () => []);
  const d = buffer.data;
  for (let s = 0; s < buffer.count; s++) {
    const o = s * STRIDE;
    const segment = [d[o + F_GEN_COL]];
    if (d[o + F_SRC] !== -1) {
      segment.push(d[o + F_SRC], d[o + F_ORIG_LINE], d[o + F_ORIG_COL]);
      if (d[o + F_NAME] !== -1) segment.push(d[o + F_NAME]);
    }
    lines[d[o + F_GEN_LINE]].push(segment);
  }
  return lines;
}

// ---------------------------------------------------------------------------
// Source normalization
// ---------------------------------------------------------------------------

const URL_SCHEME = /^[a-z][a-z0-9+.-]+:\/\//i;
const ANY_SCHEME = /^[a-z][a-z0-9+.-]+:/i;
const WEBPACK_PREFIX = /^webpack(?:-internal)?:\/\/[^/]*\/?(.*)$/i;
const DRIVE_PATH = /^[a-z]:\//i;

/**
 * Collapse `.`, `..` and duplicate slashes in a forward-slash path.
 * @param {string} p
 */
function normalizePath(p) {
  const drive = DRIVE_PATH.test(p) ? p.slice(0, 2) : '';
  const rest = drive ? p.slice(2) : p;
  const absolute = rest.startsWith('/');
  /** @type {string[]} */
  const out = [];
  for (const segment of rest.split('/')) {
    if (segment === '' || segment === '.') continue;
    if (segment === '..') {
      if (out.length && out[out.length - 1] !== '..') out.pop();
      else if (!absolute) out.push('..');
      continue;
    }
    out.push(segment);
  }
  return drive + (absolute ? '/' : '') + out.join('/');
}

/** @param {string} p */
function isAbsoluteLike(p) {
  return p.startsWith('/') || DRIVE_PATH.test(p) || ANY_SCHEME.test(p);
}

/** @param {string} url */
function fileUrlToPath(url) {
  try {
    return fileURLToPath(url).replace(/\\/g, '/');
  } catch {
    return decodeSafe(url.replace(/^file:\/\/[^/]*/i, ''));
  }
}

/** @param {string} s */
function decodeSafe(s) {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/**
 * Resolve a relative reference against a base (URL or absolute path).
 * @param {string} relative
 * @param {string | undefined} base
 * @returns {string | null}
 */
function resolveAgainst(relative, base) {
  if (!base || /^data:/i.test(base)) return null;
  if (URL_SCHEME.test(base)) {
    try {
      return new URL(relative, base).href;
    } catch {
      return null;
    }
  }
  const normalizedBase = base.replace(/\\/g, '/');
  if (normalizedBase.startsWith('/') || DRIVE_PATH.test(normalizedBase)) {
    const dir = normalizedBase.slice(0, normalizedBase.lastIndexOf('/') + 1);
    return normalizePath(dir + relative);
  }
  return null;
}

/**
 * Canonicalize a source reference into the identifier exposed by `sources`.
 * @param {string} source
 * @param {string | undefined} base
 * @returns {string}
 */
function canonicalize(source, base) {
  const webpack = WEBPACK_PREFIX.exec(source);
  if (webpack) return normalizePath(webpack[1]);
  if (/^file:\/\//i.test(source)) return normalizePath(fileUrlToPath(source));
  if (/^https?:\/\//i.test(source)) {
    try {
      const url = new URL(source);
      // Vite serves files outside the project root as /@fs/<absolute path>.
      if (url.pathname.startsWith('/@fs/')) return normalizePath(decodeSafe(url.pathname.slice(4)));
      return url.href;
    } catch {
      return source;
    }
  }
  if (source.startsWith('/@fs/')) return normalizePath(source.slice(4).replace(/\?.*$/, ''));
  if (ANY_SCHEME.test(source)) return source;
  if (source.startsWith('/') || DRIVE_PATH.test(source)) return normalizePath(source);
  const resolved = resolveAgainst(source, base);
  if (resolved !== null && resolved !== source) return canonicalize(resolved, undefined);
  return normalizePath(source);
}

const METRO_PROJECT = /^\/\[metro-project\]\//;
const METRO_WATCH_FOLDER = /^\/\[metro-watchFolders\]\/(\d+)\//;

/**
 * Rewrite Metro url-server tokens to filesystem roots when they are known.
 * @param {string} source
 * @param {MetroOptions | undefined} metro
 */
function rewriteMetro(source, metro) {
  if (!metro) return source;
  if (typeof metro.projectRoot === 'string' && METRO_PROJECT.test(source)) {
    return `${metro.projectRoot.replace(/\\/g, '/').replace(/\/?$/, '/')}${source.replace(METRO_PROJECT, '')}`;
  }
  const watch = METRO_WATCH_FOLDER.exec(source);
  const folder = watch && Array.isArray(metro.watchFolders) ? metro.watchFolders[Number(watch[1])] : undefined;
  if (typeof folder === 'string') {
    return `${folder.replace(/\\/g, '/').replace(/\/?$/, '/')}${source.slice(watch[0].length)}`;
  }
  return source;
}

/**
 * Apply sourceRoot, resolve relative sources and strip bundler prefixes.
 * @param {unknown} raw
 * @param {unknown} sourceRoot
 * @param {ResolveContext} ctx
 */
function normalizeSource(raw, sourceRoot, ctx) {
  const { base, metro } = ctx;
  let source = typeof raw === 'string' ? raw.replace(/\\/g, '/') : '';
  if (typeof sourceRoot === 'string' && sourceRoot !== '' && !isAbsoluteLike(source)) {
    const root = sourceRoot.replace(/\\/g, '/');
    source = (root.endsWith('/') ? root : `${root}/`) + source;
  }
  return canonicalize(rewriteMetro(source, metro), base);
}

/** Bundler pseudo-sources that never correspond to a user file. */
const PSEUDO_SOURCE = /^(?:external\s|webpack\/(?:runtime|bootstrap)\b|\(webpack\)|webpack\/(?:universalModuleDefinition|startup)\b)/;

/**
 * Key used for matching user paths: query/hash stripped, URL reduced to its pathname,
 * unresolved Metro tokens and Vite /@fs prefixes removed. Null for pseudo-sources.
 * @param {string} source
 * @returns {string | null}
 */
function matchKeyOf(source) {
  if (source === '' || PSEUDO_SOURCE.test(source)) return null;
  let key = source;
  const scheme = /^[a-z][a-z0-9+.-]+:\/\/[^/?#]*(.*)$/i.exec(key);
  if (scheme) key = decodeSafe(scheme[1].replace(/[?#].*$/, ''));
  else key = key.replace(/\?.*$/, '');
  if (key.startsWith('/@fs/')) key = key.slice(4);
  // Unresolved Metro tokens: keep only the project-relative remainder for suffix matching.
  key = key.replace(METRO_PROJECT, '').replace(METRO_WATCH_FOLDER, '');
  return normalizePath(key);
}

/** @param {string | null} key */
function segmentsOf(key) {
  if (key === null) return [];
  return key.split('/').filter((s) => s !== '' && s !== '.');
}

// ---------------------------------------------------------------------------
// SourceMapIndex
// ---------------------------------------------------------------------------

/**
 * @typedef {{ line: number, column: number, originalLine: number, originalColumn: number, snappedLine?: number }} GeneratedPosition
 * @typedef {{ source: string, sourceIdx: number, line: number, column: number, name: string | null }} OriginalPosition
 * @typedef {{ projectRoot?: string, watchFolders?: string[] }} MetroOptions
 * @typedef {{ mapUrl?: string, scriptUrl?: string, metro?: MetroOptions }} FromJSONOptions
 * @typedef {{ base: string | undefined, metro: MetroOptions | undefined }} ResolveContext
 */

/** Indexed, queryable view over a v3 (standard or indexed/sectioned) source map. */
export class SourceMapIndex {
  /** @type {Int32Array} */ #data;
  /** @type {number} */ #count;
  /** @type {Int32Array} */ #lineStarts;
  /** @type {string[]} */ #sources;
  /** @type {string[]} */ #names;
  /** @type {(string | null)[]} */ #sourcesContent;
  /** @type {(string | null)[]} */ #matchKeys;
  /** @type {string[][]} */ #matchSegments;
  /** @type {{ starts: Int32Array, order: Int32Array } | null} */ #bySource = null;
  /** @type {Map<number, Int32Array>} */ #sortedBySource = new Map();

  /**
   * @param {{ data: Int32Array, count: number, sources: string[], names: string[], sourcesContent: (string | null)[] }} parts
   */
  constructor(parts) {
    this.#sources = parts.sources;
    this.#names = parts.names;
    this.#sourcesContent = parts.sourcesContent;
    this.#matchKeys = parts.sources.map(matchKeyOf);
    this.#matchSegments = this.#matchKeys.map(segmentsOf);
    const { data, count } = ensureGeneratedOrder(parts.data, parts.count);
    this.#data = data;
    this.#count = count;
    this.#lineStarts = buildLineStarts(data, count);
  }

  /**
   * Parse and index a source map.
   * @param {string | object} mapJsonOrString raw JSON text or an already-parsed object
   * @param {FromJSONOptions} [options] mapUrl resolves relative sources; scriptUrl is the fallback when
   *   mapUrl is absent or a data: URL; metro maps `/[metro-project]/` and `/[metro-watchFolders]/N/` to roots
   * @returns {SourceMapIndex}
   */
  static fromJSON(mapJsonOrString, options = {}) {
    const map = parseMapInput(mapJsonOrString);
    const mapUrl = typeof options.mapUrl === 'string' ? options.mapUrl : undefined;
    const scriptUrl = typeof options.scriptUrl === 'string' ? options.scriptUrl : undefined;
    const metro = options.metro && typeof options.metro === 'object' ? options.metro : undefined;
    /** @type {ResolveContext} */
    const ctx = { base: mapUrl && !/^data:/i.test(mapUrl) ? mapUrl : scriptUrl, metro };

    const collector = new SourceCollector();
    if (Array.isArray(map.sections)) {
      const totalLength = map.sections.reduce(
        (sum, s) => sum + (typeof s?.map?.mappings === 'string' ? s.map.mappings.length : 0),
        0,
      );
      const buffer = new SegmentBuffer(totalLength >> 3);
      map.sections.forEach((section, i) => appendSection(section, i, buffer, collector, ctx));
      return new SourceMapIndex({ data: buffer.data, count: buffer.count, ...collector.result() });
    }
    validateStandardMap(map);
    const buffer = new SegmentBuffer(map.mappings.length >> 3);
    appendMap(map, buffer, collector, ctx, 0, 0);
    return new SourceMapIndex({ data: buffer.data, count: buffer.count, ...collector.result() });
  }

  /** Resolved & normalized source identifiers (absolute paths, http(s) URLs, or bundler-relative paths). */
  get sources() {
    return this.#sources.slice();
  }

  /** Symbol names referenced by mappings. */
  get names() {
    return this.#names.slice();
  }

  /** Highest generated line (0-based) carrying a mapping segment, or -1 for an empty map. */
  get lastGeneratedLine() {
    return this.#count === 0 ? -1 : this.#data[(this.#count - 1) * STRIDE + F_GEN_LINE];
  }

  /** Embedded source text per source, or null when absent. */
  get sourcesContent() {
    return this.#sourcesContent.slice();
  }

  /**
   * Best matching source index for a user-supplied path, or -1 when none or ambiguous.
   * Use `findSources` to list ambiguous candidates.
   * @param {string} userPath
   * @param {{ cwd?: string }} [options]
   * @returns {number}
   */
  findSource(userPath, options = {}) {
    const candidates = this.findSources(userPath, options);
    return candidates.length === 1 ? candidates[0] : -1;
  }

  /**
   * All best-scoring source indices for a user-supplied path (empty when none match).
   * Absolute paths (or relative paths joined with `cwd`) are matched exactly first;
   * otherwise the candidate sharing the longest trailing path-segment run wins.
   * @param {string} userPath
   * @param {{ cwd?: string }} [options]
   * @returns {number[]}
   */
  findSources(userPath, options = {}) {
    if (typeof userPath !== 'string' || userPath.trim() === '') {
      throw new SourceMapError('findSources: userPath must be a non-empty string');
    }
    let wanted = userPath.trim().replace(/\\/g, '/');
    if (/^file:\/\//i.test(wanted)) wanted = fileUrlToPath(wanted);
    wanted = normalizePath(wanted.replace(/\?.*$/, ''));

    const exactTargets = [];
    if (wanted.startsWith('/') || DRIVE_PATH.test(wanted)) exactTargets.push(wanted);
    else if (typeof options.cwd === 'string' && options.cwd !== '') {
      exactTargets.push(normalizePath(`${options.cwd.replace(/\\/g, '/')}/${wanted}`));
    }
    for (const target of exactTargets) {
      const exact = [];
      this.#matchKeys.forEach((key, i) => {
        if (key === target) exact.push(i);
      });
      if (exact.length) return exact;
    }

    const wantedSegments = segmentsOf(wanted).filter((s) => s !== '..');
    if (wantedSegments.length === 0) return [];
    let best = 0;
    /** @type {number[]} */
    let matches = [];
    this.#matchSegments.forEach((segments, i) => {
      const limit = Math.min(segments.length, wantedSegments.length);
      let common = 0;
      while (
        common < limit &&
        segments[segments.length - 1 - common] === wantedSegments[wantedSegments.length - 1 - common]
      ) {
        common++;
      }
      // One path must be a full segment-suffix of the other.
      if (common === 0 || common !== limit) return;
      if (common > best) {
        best = common;
        matches = [i];
      } else if (common === best) {
        matches.push(i);
      }
    });
    return matches;
  }

  /**
   * Generated positions for an original location.
   *
   * The segments of the original line are split into generated "chunks": runs of segments that are
   * contiguous in generated order with nothing in between except unmapped segments or segments of the
   * same original line. Inlined/duplicated code therefore yields several chunks and several results.
   *
   * Without `column`, each chunk contributes its earliest-column mapping (ties: earliest generated).
   * With `column`, the target column is an exact match if mapped, else the nearest following mapped
   * column, else the last mapped column before it; each chunk containing it contributes one result.
   * If the line has no mappings, snaps forward up to 5 lines and sets `snappedLine` on each result.
   * Results are deduplicated and ordered by generated position.
   *
   * @param {number} sourceIdx
   * @param {number} line 0-based original line
   * @param {number} [column] 0-based original column
   * @returns {GeneratedPosition[]}
   */
  originalToGenerated(sourceIdx, line, column) {
    if (!Number.isInteger(sourceIdx) || sourceIdx < 0 || sourceIdx >= this.#sources.length) {
      throw new SourceMapError(`originalToGenerated: source index ${sourceIdx} out of range`);
    }
    assertPosition('originalToGenerated', line, column ?? 0);
    const sorted = this.#sortedSegmentsFor(sourceIdx);
    const d = this.#data;

    for (let target = line; target <= line + SNAP_LINES; target++) {
      const start = lowerBound(sorted, (seg) => d[seg * STRIDE + F_ORIG_LINE] < target);
      let end = start;
      while (end < sorted.length && d[sorted[end] * STRIDE + F_ORIG_LINE] === target) end++;
      if (start === end) continue;

      const targetColumn = column === undefined ? undefined : chooseColumn(sorted, start, end, d, column);
      const lineSegments = Array.from(sorted.subarray(start, end)).sort((a, b) => a - b);
      /** @type {GeneratedPosition[]} */
      const results = [];
      const seen = new Set();
      for (const chunk of this.#splitChunks(lineSegments, sourceIdx, target)) {
        let pick = -1;
        for (const seg of chunk) {
          const col = d[seg * STRIDE + F_ORIG_COL];
          if (targetColumn === undefined) {
            if (pick === -1 || col < d[pick * STRIDE + F_ORIG_COL]) pick = seg;
          } else if (col === targetColumn) {
            pick = seg;
            break;
          }
        }
        if (pick === -1) continue;
        const genLine = d[pick * STRIDE + F_GEN_LINE];
        const genCol = d[pick * STRIDE + F_GEN_COL];
        const key = `${genLine}:${genCol}`;
        if (seen.has(key)) continue;
        seen.add(key);
        /** @type {GeneratedPosition} */
        const position = {
          line: genLine,
          column: genCol,
          originalLine: target,
          originalColumn: d[pick * STRIDE + F_ORIG_COL],
        };
        if (target !== line) position.snappedLine = target;
        results.push(position);
      }
      return results;
    }
    return [];
  }

  /**
   * Original position for a generated location, using the greatest segment with genCol <= column
   * on that generated line. Returns null when no such mapped segment exists.
   * @param {number} line 0-based generated line
   * @param {number} column 0-based generated column
   * @returns {OriginalPosition | null}
   */
  generatedToOriginal(line, column) {
    assertPosition('generatedToOriginal', line, column);
    if (line >= this.#lineStarts.length - 1) return null;
    const d = this.#data;
    let lo = this.#lineStarts[line];
    let hi = this.#lineStarts[line + 1];
    let found = -1;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (d[mid * STRIDE + F_GEN_COL] <= column) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    if (found === -1) return null;
    const o = found * STRIDE;
    const sourceIdx = d[o + F_SRC];
    if (sourceIdx === -1) return null;
    const nameIdx = d[o + F_NAME];
    return {
      source: this.#sources[sourceIdx],
      sourceIdx,
      line: d[o + F_ORIG_LINE],
      column: d[o + F_ORIG_COL],
      name: nameIdx === -1 ? null : this.#names[nameIdx],
    };
  }

  /**
   * Split segments of one original line (ascending segment indices) into generated chunks.
   * @param {number[]} segments
   * @param {number} sourceIdx
   * @param {number} line
   * @returns {number[][]}
   */
  #splitChunks(segments, sourceIdx, line) {
    const d = this.#data;
    /** @type {number[][]} */
    const chunks = [];
    let previous = -1;
    for (const seg of segments) {
      let sameChunk = previous !== -1;
      for (let j = previous + 1; sameChunk && j < seg; j++) {
        const src = d[j * STRIDE + F_SRC];
        if (src !== -1 && (src !== sourceIdx || d[j * STRIDE + F_ORIG_LINE] !== line)) sameChunk = false;
      }
      if (sameChunk) chunks[chunks.length - 1].push(seg);
      else chunks.push([seg]);
      previous = seg;
    }
    return chunks;
  }

  /**
   * Segment indices of one source sorted by (origLine, origCol, generated order); built lazily.
   * @param {number} sourceIdx
   */
  #sortedSegmentsFor(sourceIdx) {
    const cached = this.#sortedBySource.get(sourceIdx);
    if (cached) return cached;
    if (!this.#bySource) this.#bySource = groupBySource(this.#data, this.#count, this.#sources.length);
    const { starts, order } = this.#bySource;
    const d = this.#data;
    const sorted = order.slice(starts[sourceIdx], starts[sourceIdx + 1]);
    sorted.sort(
      (a, b) =>
        d[a * STRIDE + F_ORIG_LINE] - d[b * STRIDE + F_ORIG_LINE] ||
        d[a * STRIDE + F_ORIG_COL] - d[b * STRIDE + F_ORIG_COL] ||
        a - b,
    );
    this.#sortedBySource.set(sourceIdx, sorted);
    return sorted;
  }
}

/** Accumulates normalized sources across sections, deduplicating identical identifiers. */
class SourceCollector {
  constructor() {
    /** @type {string[]} */ this.sources = [];
    /** @type {(string | null)[]} */ this.sourcesContent = [];
    /** @type {string[]} */ this.names = [];
    /** @type {Map<string, number>} */ this.indexOf = new Map();
  }

  /**
   * @param {string} source
   * @param {unknown} content
   */
  add(source, content) {
    let index = this.indexOf.get(source);
    if (index === undefined) {
      index = this.sources.length;
      this.indexOf.set(source, index);
      this.sources.push(source);
      this.sourcesContent.push(typeof content === 'string' ? content : null);
    } else if (this.sourcesContent[index] === null && typeof content === 'string') {
      this.sourcesContent[index] = content;
    }
    return index;
  }

  result() {
    return { sources: this.sources, names: this.names, sourcesContent: this.sourcesContent };
  }
}

/**
 * @param {unknown} input
 * @returns {any}
 */
function parseMapInput(input) {
  let map = input;
  if (typeof input === 'string') {
    // Strip the XSSI guard some servers prepend.
    const text = input.replace(/^\)\]\}'[^\n]*\n/, '');
    try {
      map = JSON.parse(text);
    } catch (error) {
      throw new SourceMapError(`Invalid source map JSON: ${/** @type {Error} */ (error).message}`, { cause: error });
    }
  }
  if (map === null || typeof map !== 'object' || Array.isArray(map)) {
    throw new SourceMapError('Invalid source map: expected a JSON object');
  }
  const version = /** @type {any} */ (map).version;
  if (version !== undefined && version !== 3) {
    throw new SourceMapError(`Unsupported source map version: ${JSON.stringify(version)}`);
  }
  return map;
}

/** @param {any} map */
function validateStandardMap(map) {
  if (typeof map.mappings !== 'string') {
    throw new SourceMapError('Invalid source map: "mappings" must be a string');
  }
  if (map.sources !== undefined && !Array.isArray(map.sources)) {
    throw new SourceMapError('Invalid source map: "sources" must be an array');
  }
  if (map.names !== undefined && !Array.isArray(map.names)) {
    throw new SourceMapError('Invalid source map: "names" must be an array');
  }
}

/**
 * @param {any} section
 * @param {number} i
 * @param {SegmentBuffer} buffer
 * @param {SourceCollector} collector
 * @param {ResolveContext} ctx
 */
function appendSection(section, i, buffer, collector, ctx) {
  if (section === null || typeof section !== 'object') {
    throw new SourceMapError(`Invalid indexed map: section ${i} is not an object`);
  }
  const offset = section.offset;
  if (!offset || !Number.isInteger(offset.line) || !Number.isInteger(offset.column) || offset.line < 0 || offset.column < 0) {
    throw new SourceMapError(`Invalid indexed map: section ${i} has an invalid offset`);
  }
  if (section.url !== undefined && section.map === undefined) {
    throw new SourceMapError(`Unsupported indexed map: section ${i} references an external url`);
  }
  const map = parseMapInput(section.map);
  if (map.sections !== undefined) {
    throw new SourceMapError(`Invalid indexed map: section ${i} contains nested sections`);
  }
  validateStandardMap(map);
  appendMap(map, buffer, collector, ctx, offset.line, offset.column);
}

/**
 * @param {any} map
 * @param {SegmentBuffer} buffer
 * @param {SourceCollector} collector
 * @param {ResolveContext} ctx
 * @param {number} lineOffset
 * @param {number} columnOffset
 */
function appendMap(map, buffer, collector, ctx, lineOffset, columnOffset) {
  const rawSources = map.sources ?? [];
  const contents = Array.isArray(map.sourcesContent) ? map.sourcesContent : [];
  const sourceMap = new Int32Array(rawSources.length);
  rawSources.forEach((raw, i) => {
    sourceMap[i] = collector.add(normalizeSource(raw, map.sourceRoot, ctx), contents[i]);
  });
  const names = map.names ?? [];
  const nameBase = collector.names.length;
  for (const name of names) collector.names.push(typeof name === 'string' ? name : String(name));
  decodeInto(map.mappings, buffer, {
    sourcesLength: rawSources.length,
    namesLength: names.length,
    lineOffset,
    columnOffset,
    sourceMap,
    nameBase,
  });
}

/**
 * Return segments sorted by (genLine, genCol). Valid maps are already ordered; fall back to a
 * stable sort for out-of-order sections or segments.
 * @param {Int32Array} data
 * @param {number} count
 */
function ensureGeneratedOrder(data, count) {
  let ordered = true;
  for (let s = 1; s < count && ordered; s++) {
    const prev = (s - 1) * STRIDE;
    const cur = s * STRIDE;
    const lineDelta = data[cur + F_GEN_LINE] - data[prev + F_GEN_LINE];
    if (lineDelta < 0 || (lineDelta === 0 && data[cur + F_GEN_COL] < data[prev + F_GEN_COL])) ordered = false;
  }
  if (ordered) return { data, count };
  const order = Array.from({ length: count }, (_, s) => s);
  order.sort(
    (a, b) =>
      data[a * STRIDE + F_GEN_LINE] - data[b * STRIDE + F_GEN_LINE] ||
      data[a * STRIDE + F_GEN_COL] - data[b * STRIDE + F_GEN_COL] ||
      a - b,
  );
  const sorted = new Int32Array(count * STRIDE);
  order.forEach((from, to) => sorted.set(data.subarray(from * STRIDE, from * STRIDE + STRIDE), to * STRIDE));
  return { data: sorted, count };
}

/**
 * lineStarts[l]..lineStarts[l + 1] is the segment range of generated line l.
 * @param {Int32Array} data
 * @param {number} count
 */
function buildLineStarts(data, count) {
  const lineCount = count === 0 ? 0 : data[(count - 1) * STRIDE + F_GEN_LINE] + 1;
  const starts = new Int32Array(lineCount + 1);
  for (let s = 0; s < count; s++) starts[data[s * STRIDE + F_GEN_LINE] + 1]++;
  for (let l = 0; l < lineCount; l++) starts[l + 1] += starts[l];
  return starts;
}

/**
 * Counting-sort segment indices by source (generated order preserved within a source).
 * @param {Int32Array} data
 * @param {number} count
 * @param {number} sourceCount
 */
function groupBySource(data, count, sourceCount) {
  const starts = new Int32Array(sourceCount + 1);
  for (let s = 0; s < count; s++) {
    const src = data[s * STRIDE + F_SRC];
    if (src !== -1) starts[src + 1]++;
  }
  for (let i = 0; i < sourceCount; i++) starts[i + 1] += starts[i];
  const cursor = starts.slice(0, sourceCount);
  const order = new Int32Array(starts[sourceCount]);
  for (let s = 0; s < count; s++) {
    const src = data[s * STRIDE + F_SRC];
    if (src !== -1) order[cursor[src]++] = s;
  }
  return { starts, order };
}

/**
 * First index in `arr` for which `isBefore` is false.
 * @param {Int32Array} arr
 * @param {(value: number) => boolean} isBefore
 */
function lowerBound(arr, isBefore) {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (isBefore(arr[mid])) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Pick the original column to resolve within [start, end) (sorted by column).
 * @param {Int32Array} sorted
 * @param {number} start
 * @param {number} end
 * @param {Int32Array} d
 * @param {number | undefined} column
 */
function chooseColumn(sorted, start, end, d, column) {
  const first = d[sorted[start] * STRIDE + F_ORIG_COL];
  if (column === undefined) return first;
  for (let k = start; k < end; k++) {
    const col = d[sorted[k] * STRIDE + F_ORIG_COL];
    if (col >= column) return col;
  }
  return d[sorted[end - 1] * STRIDE + F_ORIG_COL];
}

/**
 * @param {string} method
 * @param {unknown} line
 * @param {unknown} column
 */
function assertPosition(method, line, column) {
  if (!Number.isInteger(line) || /** @type {number} */ (line) < 0) {
    throw new SourceMapError(`${method}: line must be a non-negative integer (0-based), got ${String(line)}`);
  }
  if (!Number.isInteger(column) || /** @type {number} */ (column) < 0) {
    throw new SourceMapError(`${method}: column must be a non-negative integer (0-based), got ${String(column)}`);
  }
}

// ---------------------------------------------------------------------------
// Script helpers
// ---------------------------------------------------------------------------

/**
 * Last `//# sourceMappingURL=` (or legacy `//@`, or block-comment form) value in a script, or null.
 * @param {string} scriptSource
 * @returns {string | null}
 */
export function parseSourceMappingURL(scriptSource) {
  if (typeof scriptSource !== 'string') return null;
  const marker = 'sourceMappingURL=';
  let pos = scriptSource.length;
  while (pos > 0) {
    pos = scriptSource.lastIndexOf(marker, pos - 1);
    if (pos === -1) break;
    const lineStart = scriptSource.lastIndexOf('\n', pos) + 1;
    const prefix = scriptSource.slice(lineStart, pos);
    if (/^\s*(?:\/\/|\/\*)[#@][ \t]+$/.test(prefix)) {
      const valuePattern = /[^\s'"]+/y;
      valuePattern.lastIndex = pos + marker.length;
      const match = valuePattern.exec(scriptSource);
      const value = match ? match[0].replace(/\*\/$/, '') : '';
      if (value) return value;
    }
  }
  return null;
}

/**
 * Decode a base64 JSON data URL (`data:application/json;base64,...`, optionally with `;charset=...`).
 * @param {string} url
 * @returns {string | null} the decoded JSON text, or null for any other URL
 */
export function decodeDataUrl(url) {
  if (typeof url !== 'string') return null;
  const match = /^data:application\/json(?:;charset=[^;,]*)?;base64,/i.exec(url);
  if (!match) return null;
  return Buffer.from(url.slice(match[0].length), 'base64').toString('utf8');
}
