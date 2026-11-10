import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { SourceMap } from 'node:module';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

import {
  SourceMapError,
  SourceMapIndex,
  decodeDataUrl,
  decodeMappings,
  parseSourceMappingURL,
} from '../lib/sourcemap.mjs';

const FIXTURES = path.join(import.meta.dirname, 'fixtures');
const TS_FIXTURE_JS = path.join(FIXTURES, 'ts', 'dist', 'order-service.js');
const TS_FIXTURE_MAP = path.join(FIXTURES, 'ts', 'dist', 'order-service.js.map');
const TS_FIXTURE_SRC = path.join(FIXTURES, 'ts', 'src', 'order-service.ts');

// ---------------------------------------------------------------------------
// Test-only encoder: builds v3 `mappings` from absolute segments.
// ---------------------------------------------------------------------------

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

/** @param {number} value */
function encodeVLQ(value) {
  let vlq = value < 0 ? (-value << 1) | 1 : value << 1;
  let out = '';
  do {
    let digit = vlq & 31;
    vlq >>>= 5;
    if (vlq > 0) digit |= 32;
    out += B64[digit];
  } while (vlq > 0);
  return out;
}

/**
 * @param {number[][][]} lines absolute segments per generated line
 */
function encodeMappings(lines) {
  let src = 0;
  let origLine = 0;
  let origCol = 0;
  let name = 0;
  return lines
    .map((segments) => {
      let genCol = 0;
      return segments
        .map((seg) => {
          let out = encodeVLQ(seg[0] - genCol);
          genCol = seg[0];
          if (seg.length >= 4) {
            out += encodeVLQ(seg[1] - src) + encodeVLQ(seg[2] - origLine) + encodeVLQ(seg[3] - origCol);
            [, src, origLine, origCol] = seg;
            if (seg.length === 5) {
              out += encodeVLQ(seg[4] - name);
              name = seg[4];
            }
          }
          return out;
        })
        .join(',');
    })
    .join(';');
}

/** @param {object} overrides */
function makeMap(overrides) {
  return { version: 3, sources: ['a.ts'], names: [], mappings: '', ...overrides };
}

// ---------------------------------------------------------------------------
// VLQ decoding
// ---------------------------------------------------------------------------

test('decodeMappings decodes single-char, multi-char and negative VLQ values', () => {
  // Hand-checked: 'C' = +1, 'D' = -1, 'K' = +5, 'gB' = +16, '2H' = +123.
  assert.deepEqual(decodeMappings('AAAA'), [[[0, 0, 0, 0]]]);
  assert.deepEqual(decodeMappings('gBAAC'), [[[16, 0, 0, 1]]]);
  assert.deepEqual(decodeMappings('AAAK,2HAAD'), [[[0, 0, 0, 5], [123, 0, 0, 4]]]);
  assert.deepEqual(decodeMappings('AAAAC,CAACD'), [[[0, 0, 0, 0, 1], [1, 0, 0, 1, 0]]]);
});

test('decodeMappings round-trips large and negative deltas across lines', () => {
  const lines = [
    [[0, 0, 0, 0], [5, 1, 10, 3, 2]],
    [],
    [[1000000, 0, 2, 0]],
    [[4], [7, 2, 100000, 65535, 0]],
    [[0, 1, 3, 1]],
  ];
  assert.deepEqual(decodeMappings(encodeMappings(lines)), lines);
});

test('decodeMappings rejects malformed input with SourceMapError', () => {
  for (const bad of ['AA', 'AAA', 'AAAAAA', 'AA!A', 'g', 'gggggggggA', 'AAAA,D']) {
    assert.throws(() => decodeMappings(bad), SourceMapError, `expected rejection of ${bad}`);
  }
  assert.throws(() => decodeMappings(/** @type {any} */ (42)), SourceMapError);
});

// ---------------------------------------------------------------------------
// Standard and indexed maps
// ---------------------------------------------------------------------------

test('standard map translates generated positions to original with names', () => {
  const index = SourceMapIndex.fromJSON(
    JSON.stringify(
      makeMap({
        sources: ['a.ts', 'b.ts'],
        names: ['foo', 'bar'],
        mappings: encodeMappings([[[0, 0, 0, 0], [10, 0, 0, 4, 0]], [[2, 1, 5, 2, 1]]]),
      }),
    ),
    { mapUrl: '/dist/app.js.map' },
  );
  assert.deepEqual(index.sources, ['/dist/a.ts', '/dist/b.ts']);
  assert.deepEqual(index.generatedToOriginal(0, 12), { source: '/dist/a.ts', sourceIdx: 0, line: 0, column: 4, name: 'foo' });
  assert.deepEqual(index.generatedToOriginal(1, 2), { source: '/dist/b.ts', sourceIdx: 1, line: 5, column: 2, name: 'bar' });
  assert.equal(index.generatedToOriginal(0, 3)?.column, 0);
});

test('lastGeneratedLine is the highest mapped generated line, ignoring trailing unmapped lines', () => {
  const index = SourceMapIndex.fromJSON(makeMap({ mappings: `${encodeMappings([[[0, 0, 0, 0]], [], [[4, 0, 3, 0]]])};;` }));
  assert.equal(index.lastGeneratedLine, 2);
  assert.equal(SourceMapIndex.fromJSON(makeMap({ mappings: ';;' })).lastGeneratedLine, -1);
});

test('generatedToOriginal returns null before the first segment, on unmapped segments and past the end', () => {
  const index = SourceMapIndex.fromJSON(makeMap({ mappings: encodeMappings([[[4, 0, 0, 0], [9]], [[0, 0, 1, 0]]]) }));
  assert.equal(index.generatedToOriginal(0, 3), null);
  assert.equal(index.generatedToOriginal(0, 4)?.line, 0);
  assert.equal(index.generatedToOriginal(0, 50), null);
  assert.equal(index.generatedToOriginal(7, 0), null);
  assert.throws(() => index.generatedToOriginal(-1, 0), SourceMapError);
});

test('indexed map applies section line and column offsets and merges sources', () => {
  const index = SourceMapIndex.fromJSON({
    version: 3,
    sections: [
      { offset: { line: 0, column: 0 }, map: makeMap({ sources: ['a.ts'], mappings: encodeMappings([[[0, 0, 0, 0]]]) }) },
      {
        offset: { line: 2, column: 10 },
        map: makeMap({ sources: ['b.ts', 'a.ts'], names: ['x'], mappings: encodeMappings([[[0, 0, 3, 0, 0], [5, 1, 7, 1]], [[1, 0, 4, 0]]]) }),
      },
    ],
  }, { mapUrl: 'http://localhost:3000/assets/app.js.map' });
  assert.deepEqual(index.sources, ['http://localhost:3000/assets/a.ts', 'http://localhost:3000/assets/b.ts']);
  assert.deepEqual(index.generatedToOriginal(2, 10), { source: 'http://localhost:3000/assets/b.ts', sourceIdx: 1, line: 3, column: 0, name: 'x' });
  assert.equal(index.generatedToOriginal(2, 9), null, 'column offset only shifts the first section line');
  assert.equal(index.generatedToOriginal(2, 15)?.sourceIdx, 0);
  assert.equal(index.generatedToOriginal(3, 1)?.line, 4, 'later section lines are not column-shifted');
  assert.deepEqual(index.originalToGenerated(0, 7), [{ line: 2, column: 15, originalLine: 7, originalColumn: 1 }]);
});

test('fromJSON rejects malformed maps with SourceMapError', () => {
  const cases = [
    '{not json',
    '[]',
    JSON.stringify({ version: 2, sources: [], mappings: '' }),
    JSON.stringify({ version: 3, sources: [] }),
    JSON.stringify(makeMap({ mappings: 'ACAA' })),
    JSON.stringify(makeMap({ mappings: 'AAAAC' })),
    JSON.stringify({ version: 3, sections: [{ offset: { line: 0, column: 0 }, url: 'x.map' }] }),
    JSON.stringify({ version: 3, sections: [{ offset: { line: -1, column: 0 }, map: makeMap({}) }] }),
  ];
  for (const input of cases) assert.throws(() => SourceMapIndex.fromJSON(input), SourceMapError, input);
});

test('fromJSON accepts the XSSI guard prefix', () => {
  const index = SourceMapIndex.fromJSON(`)]}'\n${JSON.stringify(makeMap({ mappings: 'AAAA' }))}`);
  assert.equal(index.generatedToOriginal(0, 0)?.sourceIdx, 0);
});

// ---------------------------------------------------------------------------
// Source normalization
// ---------------------------------------------------------------------------

test('webpack:// prefixes are stripped, keeping the bundler-relative path', () => {
  const index = SourceMapIndex.fromJSON(
    makeMap({
      sources: [
        'webpack://client-api/./src/foo.ts',
        'webpack:///./src/bar.ts',
        'webpack:///src/baz.ts',
        'webpack://client-api/./src/q.ts?a1b2',
      ],
    }),
    { mapUrl: 'http://localhost:3000/main.js.map' },
  );
  assert.deepEqual(index.sources, ['src/foo.ts', 'src/bar.ts', 'src/baz.ts', 'src/q.ts?a1b2']);
  assert.equal(index.findSource('src/q.ts'), 3, 'query strings are ignored for matching');
});

test('file:// sources become absolute paths and sourceRoot is applied', () => {
  const index = SourceMapIndex.fromJSON(
    makeMap({ sources: ['file:///repo/apps/x/src/a.ts', 'b.ts', '/abs/c.ts'], sourceRoot: '../src/' }),
    { mapUrl: 'file:///repo/apps/x/dist/main.js.map' },
  );
  assert.deepEqual(index.sources, ['/repo/apps/x/src/a.ts', '/repo/apps/x/src/b.ts', '/abs/c.ts']);
});

test('sourceRoot with webpack scheme is normalized', () => {
  const index = SourceMapIndex.fromJSON(makeMap({ sources: ['./src/a.ts'], sourceRoot: 'webpack://app' }));
  assert.deepEqual(index.sources, ['src/a.ts']);
});

test('relative sources resolve against scriptUrl when the map is a data: URL', () => {
  const index = SourceMapIndex.fromJSON(makeMap({ sources: ['../src/a.ts'] }), {
    mapUrl: 'data:application/json;base64,e30=',
    scriptUrl: 'file:///repo/dist/main.js',
  });
  assert.deepEqual(index.sources, ['/repo/src/a.ts']);
});

test('Metro url-server tokens map to projectRoot/watchFolders when configured', () => {
  const map = makeMap({
    sources: ['/[metro-project]/src/foo.ts', '/[metro-watchFolders]/0/libs/x.ts', '/[metro-watchFolders]/1/y.ts'],
  });
  const configured = SourceMapIndex.fromJSON(map, {
    scriptUrl: 'http://localhost:8081/index.bundle?platform=ios',
    metro: { projectRoot: '/repo/apps/mobile', watchFolders: ['/repo', '/shared/'] },
  });
  assert.deepEqual(configured.sources, ['/repo/apps/mobile/src/foo.ts', '/repo/libs/x.ts', '/shared/y.ts']);
  assert.equal(configured.findSource('/repo/libs/x.ts'), 1);
  assert.deepEqual(configured.sourcesContent, [null, null, null], 'sourcesContent is optional');

  const unconfigured = SourceMapIndex.fromJSON(map, { scriptUrl: 'http://localhost:8081/index.bundle' });
  assert.equal(unconfigured.findSource('apps/mobile/src/foo.ts'), 0, 'suffix match after the metro token');
  assert.equal(unconfigured.findSource('libs/x.ts'), 1);
  assert.equal(unconfigured.findSource('/repo/libs/x.ts'), 1);
});

test('Vite inline maps resolve bare sources against scriptUrl and strip /@fs', () => {
  const app = SourceMapIndex.fromJSON(makeMap({ sources: ['x.ts'] }), {
    mapUrl: 'data:application/json;base64,e30=',
    scriptUrl: 'http://localhost:4200/src/app/x.ts?t=123',
  });
  assert.deepEqual(app.sources, ['http://localhost:4200/src/app/x.ts']);
  assert.equal(app.findSource('apps/web/src/app/x.ts'), 0);
  assert.equal(app.findSource('/Users/me/repo/apps/web/src/app/x.ts'), 0);

  const lib = SourceMapIndex.fromJSON(makeMap({ sources: ['y.ts'] }), {
    mapUrl: 'data:application/json;charset=utf-8;base64,e30=',
    scriptUrl: 'http://localhost:4200/@fs/Users/me/repo/libs/y.ts?v=9f2',
  });
  assert.deepEqual(lib.sources, ['/Users/me/repo/libs/y.ts']);
  assert.equal(lib.findSource('/Users/me/repo/libs/y.ts'), 0);
});

test('webpack pseudo-sources never match user paths', () => {
  const index = SourceMapIndex.fromJSON(
    makeMap({
      sources: [
        'webpack://client-api/external commonjs "tslib"',
        'webpack://client-api/webpack/runtime/define property getters',
        'webpack://client-api/./src/tslib',
      ],
    }),
  );
  assert.deepEqual(index.findSources('"tslib"'), []);
  assert.deepEqual(index.findSources('define property getters'), []);
  assert.deepEqual(index.findSources('webpack/runtime/define property getters'), []);
  assert.equal(index.findSource('tslib'), 2);
});

// ---------------------------------------------------------------------------
// Source matching
// ---------------------------------------------------------------------------

test('findSource suffix-matches on path segments and prefers the longest match', () => {
  const index = SourceMapIndex.fromJSON(
    makeMap({
      sources: [
        '/abs/apps/client-api/src/foo.ts',
        'webpack://app/./src/foo.ts',
        '/abs/apps/admin/src/foo.ts',
        '/abs/apps/client-api/src/myfoo.ts',
      ],
    }),
  );
  assert.equal(index.findSource('apps/client-api/src/foo.ts'), 0);
  assert.equal(index.findSource('client-api/src/foo.ts'), 0);
  assert.equal(index.findSource('/abs/apps/admin/src/foo.ts'), 2, 'absolute exact match wins');
  assert.equal(index.findSource('src/foo.ts'), -1, 'ambiguous');
  assert.deepEqual(index.findSources('src/foo.ts'), [0, 1, 2]);
  assert.equal(index.findSource('oo.ts'), -1, 'partial segment never matches');
  assert.deepEqual(index.findSources('nope.ts'), []);
  assert.throws(() => index.findSources(''), SourceMapError);
});

test('findSource resolves relative paths against cwd for an exact match first', () => {
  const index = SourceMapIndex.fromJSON(makeMap({ sources: ['/repo/a/src/foo.ts', '/repo/b/src/foo.ts'] }));
  assert.equal(index.findSource('src/foo.ts', { cwd: '/repo/b' }), 1);
  assert.equal(index.findSource('src/foo.ts'), -1);
  assert.equal(index.findSource('src\\foo.ts', { cwd: '/repo/a' }), 0, 'backslashes are normalized');
});

// ---------------------------------------------------------------------------
// originalToGenerated
// ---------------------------------------------------------------------------

test('originalToGenerated snaps forward to the next mapped line within 5 lines', () => {
  const index = SourceMapIndex.fromJSON(
    makeMap({ mappings: encodeMappings([[[0, 0, 0, 0]], [[2, 0, 4, 2], [9, 0, 4, 6]], [[2, 0, 10, 0]]]) }),
  );
  assert.deepEqual(index.originalToGenerated(0, 4), [{ line: 1, column: 2, originalLine: 4, originalColumn: 2 }]);
  assert.deepEqual(index.originalToGenerated(0, 1), [{ line: 1, column: 2, originalLine: 4, originalColumn: 2, snappedLine: 4 }]);
  assert.deepEqual(index.originalToGenerated(0, 5)[0].snappedLine, 10);
  assert.deepEqual(index.originalToGenerated(0, 11), []);
  assert.throws(() => index.originalToGenerated(3, 0), SourceMapError);
});

test('originalToGenerated with a column prefers exact, then following, then preceding columns', () => {
  const index = SourceMapIndex.fromJSON(makeMap({ mappings: encodeMappings([[[0, 0, 0, 0], [5, 0, 0, 4], [9, 0, 0, 8]]]) }));
  assert.deepEqual(index.originalToGenerated(0, 0, 4).map((p) => p.column), [5]);
  assert.deepEqual(index.originalToGenerated(0, 0, 5).map((p) => p.column), [9]);
  assert.deepEqual(index.originalToGenerated(0, 0, 20).map((p) => p.column), [9]);
});

test('originalToGenerated returns every generated chunk of an inlined/duplicated line', () => {
  // Original line 3 is emitted three times: on gen line 0, inlined into gen line 1 between other
  // code, and a contiguous duplicate mapping on gen line 1 that must collapse into its chunk.
  const index = SourceMapIndex.fromJSON(
    makeMap({
      sources: ['a.ts', 'b.ts'],
      mappings: encodeMappings([
        [[0, 0, 3, 2], [6, 0, 3, 9], [12, 0, 4, 0]],
        [[0, 1, 0, 0], [20, 0, 3, 2], [25, 0, 3, 9], [30, 0, 3, 2], [40, 1, 0, 5], [50, 0, 3, 5]],
      ]),
    }),
  );
  assert.deepEqual(index.originalToGenerated(0, 3), [
    { line: 0, column: 0, originalLine: 3, originalColumn: 2 },
    { line: 1, column: 20, originalLine: 3, originalColumn: 2 },
    { line: 1, column: 50, originalLine: 3, originalColumn: 5 },
  ]);
  assert.deepEqual(index.originalToGenerated(0, 3, 9).map((p) => [p.line, p.column]), [[0, 6], [1, 25]]);
});

// ---------------------------------------------------------------------------
// Script helpers
// ---------------------------------------------------------------------------

test('parseSourceMappingURL returns the last annotation or null', () => {
  assert.equal(parseSourceMappingURL('a();\n//# sourceMappingURL=first.map\nb();\n//# sourceMappingURL=last.js.map\n'), 'last.js.map');
  assert.equal(parseSourceMappingURL('x();\n/*# sourceMappingURL=block.map */'), 'block.map');
  assert.equal(parseSourceMappingURL('//@ sourceMappingURL=legacy.map'), 'legacy.map');
  assert.equal(parseSourceMappingURL('const s = "sourceMappingURL=nope.map";'), null);
  assert.equal(parseSourceMappingURL('no annotation'), null);
  assert.equal(parseSourceMappingURL(readFileSync(TS_FIXTURE_JS, 'utf8')), 'order-service.js.map');
});

test('decodeDataUrl decodes base64 JSON data URLs only', () => {
  const json = JSON.stringify({ version: 3, sources: ['ü.ts'] });
  const b64 = Buffer.from(json, 'utf8').toString('base64');
  assert.equal(decodeDataUrl(`data:application/json;base64,${b64}`), json);
  assert.equal(decodeDataUrl(`data:application/json;charset=utf-8;base64,${b64}`), json);
  assert.equal(decodeDataUrl(`data:application/json;charset=utf8;base64,${b64}`), json);
  assert.equal(decodeDataUrl('data:text/plain;base64,e30='), null);
  assert.equal(decodeDataUrl('http://localhost/app.js.map'), null);
});

// ---------------------------------------------------------------------------
// Realistic fixture (tsc 5.9 output) cross-checked against node:module SourceMap
// ---------------------------------------------------------------------------

function loadTsFixture() {
  const text = readFileSync(TS_FIXTURE_MAP, 'utf8');
  return {
    text,
    index: SourceMapIndex.fromJSON(text, { mapUrl: pathToFileURL(TS_FIXTURE_MAP).href }),
  };
}

test('tsc fixture: sources resolve to the absolute .ts path and breakpoints land on statements', () => {
  const { index } = loadTsFixture();
  assert.deepEqual(index.sources, [TS_FIXTURE_SRC]);
  const src = index.findSource('ts/src/order-service.ts');
  assert.equal(src, 0);
  assert.equal(index.findSource(TS_FIXTURE_SRC), 0);

  // `this.lines.push({ ...line });` (line 21, 1-based) -> `this.lines.push(Object.assign(...))`
  assert.deepEqual(index.originalToGenerated(src, 20), [{ line: 15, column: 8, originalLine: 20, originalColumn: 4 }]);
  // Blank line 20 (1-based) snaps to line 21.
  assert.equal(index.originalToGenerated(src, 19)[0].snappedLine, 20);
  // `export class Order` is emitted both as the class and as `exports.Order = Order`.
  assert.deepEqual(index.originalToGenerated(src, 8).map((p) => [p.line, p.column]), [[4, 0], [26, 0]]);
  // `export async function placeOrder` is emitted as the hoisted export and the declaration.
  assert.deepEqual(index.originalToGenerated(src, 36).map((p) => [p.line, p.column]), [[3, 0], [27, 0]]);
  // Interface declarations have no output; nothing within 5 lines of line 1.
  assert.deepEqual(index.originalToGenerated(src, 0), []);
});

test('tsc fixture: generatedToOriginal agrees with node:module SourceMap.findEntry', () => {
  const { text, index } = loadTsFixture();
  const reference = new SourceMap(JSON.parse(text));
  const generated = readFileSync(TS_FIXTURE_JS, 'utf8').split('\n');
  let compared = 0;
  generated.forEach((lineText, line) => {
    for (let column = 0; column <= lineText.length; column++) {
      const ours = index.generatedToOriginal(line, column);
      const entry = /** @type {any} */ (reference.findEntry(line, column));
      const nodeHasSameLineEntry = entry && entry.generatedLine === line && entry.originalSource !== undefined;
      assert.equal(ours !== null, Boolean(nodeHasSameLineEntry), `presence mismatch at ${line}:${column}`);
      if (!ours) continue;
      assert.equal(ours.line, entry.originalLine, `line mismatch at ${line}:${column}`);
      assert.equal(ours.column, entry.originalColumn, `column mismatch at ${line}:${column}`);
      assert.ok(entry.originalSource.endsWith('src/order-service.ts'));
      compared++;
    }
  });
  assert.ok(compared > 500, `compared ${compared} positions`);
});

test('tsc fixture: every original mapping round-trips through generatedToOriginal', () => {
  const { index } = loadTsFixture();
  for (let line = 0; line < 42; line++) {
    for (const position of index.originalToGenerated(0, line)) {
      const back = index.generatedToOriginal(position.line, position.column);
      assert.equal(back?.line, position.originalLine);
      assert.equal(back?.column, position.originalColumn);
    }
  }
});
