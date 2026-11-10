/** Single CLI contract: help, allowed flags, arity and validation. */
import { DbgError } from './ipc.mjs';
const globalFlags = ['session', 'pretty', 'max-output-bytes', 'help'];
export const COMMANDS = {
  targets: [0, 0, ['kind', 'port'], 'List inspector targets'],
  start: [0, 0, ['port', 'pid', 'host', 'kind', 'cwd', 'target', 'web-root', 'metro-root', 'watch-folders', 'idle-timeout', 'attach-timeout'], 'Attach: --port N or --pid N; Hermes defaults to 8081'],
  status: [0, 0, [], 'Show session and target state'],
  reload: [0, 0, [], 'Reload the page/app'],
  break: [1, 1, ['if'], '<file>:<line>[:<col>] [--if expr]'],
  logpoint: [2, Infinity, [], '<file>:<line>[:<col>] <expr>'],
  list: [0, 0, [], 'List probes'], remove: [1, 1, [], '<id>'], clear: [0, 0, [], 'Remove probes'],
  wait: [0, 0, ['timeout'], 'Wait for pause; default 300 seconds'],
  stack: [0, 0, ['all', 'offset', 'limit'], 'Expand hidden frames with --all; paginate with --offset/--limit (max 60 frames per page)'],
  scope: [0, 0, ['frame', 'depth', 'all', 'vars', 'scope'], 'Filter --vars a,b and --scope TYPE_OR_NAME; --all includes omitted scopes, previews remain capped'],
  eval: [1, Infinity, ['frame', 'depth'], '<expr>'], step: [1, 1, ['timeout'], 'over|into|out'],
  continue: [0, 0, [], 'Resume'], pause: [0, 0, [], 'Pause'], exceptions: [1, 1, [], 'none|uncaught|all'],
  logs: [0, 0, ['since', 'limit', 'tail'], 'Oldest unread page (default 20); use nextSeq as --since; --tail reads newest'],
  stop: [0, 0, [], 'Stop daemon'],
};
const booleanFlags = new Set(['all', 'tail', 'pretty', 'help']);
const allFlags = new Set([...globalFlags, ...Object.values(COMMANDS).flatMap((c) => c[2])]);
const bounds = { port: [1, 65535], pid: [1, 2147483647], frame: [0, 1000000], depth: [0, 5], since: [0, Number.MAX_SAFE_INTEGER], limit: [1, 10000], offset: [0, Number.MAX_SAFE_INTEGER], 'max-output-bytes': [1024, 1048576] };
export function numberFlag(value, name) {
  if (value === undefined) return undefined;
  const n = Number(value);
  const range = bounds[name] ?? [1, 86400];
  if (!value.trim() || !Number.isFinite(n) || (bounds[name] && !Number.isSafeInteger(n)) || n < range[0] || n > range[1]) {
    throw new DbgError('BAD_ARGS', `--${name} must be ${bounds[name] ? 'an integer' : 'a number'} between ${range[0]} and ${range[1]}`);
  }
  return n;
}
export function parseDuration(value) {
  const match = /^(\d+(?:\.\d+)?)(ms|s|m|h)?$/.exec(value.trim());
  const n = match && Number(match[1]) * ({ ms: 1, s: 1000, m: 60000, h: 3600000 }[match[2] ?? 's']);
  if (!n || !Number.isFinite(n) || n < 1 || n > 604800000) throw new DbgError('BAD_ARGS', '--idle-timeout must be between 1ms and 7d (use ms, s, m or h)');
  return Math.round(n);
}
export function usage(command) {
  const entries = command && COMMANDS[command] ? [[command, COMMANDS[command]]] : Object.entries(COMMANDS);
  return `Requires Node >=22. usage: dbg <command> [args] [--session name] [--pretty] [--max-output-bytes N=16384]\n` + entries.map(([name, spec]) => `  ${name} ${spec[3]}${spec[2].length ? `; flags: ${spec[2].map((f) => `--${f}`).join(' ')}` : ''}`).join('\n');
}
export function parseArgs(argv) {
  const positional = [], flags = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--') { positional.push(...argv.slice(i + 1)); break; }
    if (arg === '-h') { flags.help = 'true'; continue; }
    if (!arg.startsWith('--')) { positional.push(arg); continue; }
    const eq = arg.indexOf('=');
    const name = eq < 0 ? arg.slice(2) : arg.slice(2, eq);
    if (!allFlags.has(name)) throw new DbgError('BAD_ARGS', `unknown flag --${name}; see dbg --help`);
    if (flags[name] !== undefined) throw new DbgError('BAD_ARGS', `duplicate --${name}`);
    if (booleanFlags.has(name)) {
      if (eq >= 0) throw new DbgError('BAD_ARGS', `--${name} takes no value`);
      flags[name] = 'true'; continue;
    }
    const value = eq < 0 ? argv[++i] : arg.slice(eq + 1);
    if (value === undefined || value.startsWith('--') || value === '') throw new DbgError('BAD_ARGS', `--${name} needs a value`);
    flags[name] = value;
  }
  if (flags['max-output-bytes'] !== undefined) numberFlag(flags['max-output-bytes'], 'max-output-bytes');
  const [command, ...rest] = positional;
  if (!command) {
    if (Object.keys(flags).some((key) => !globalFlags.includes(key))) throw new DbgError('BAD_ARGS', 'missing command');
    return { positional, flags };
  }
  const spec = COMMANDS[command];
  if (!spec) throw new DbgError('BAD_ARGS', `unknown command "${command}"; see dbg --help`);
  const allowed = new Set([...globalFlags, ...spec[2]]);
  for (const [key, value] of Object.entries(flags)) {
    if (!allowed.has(key)) throw new DbgError('BAD_ARGS', `${command} does not accept --${key}`);
    if (bounds[key] || key === 'timeout' || key === 'attach-timeout') numberFlag(value, key);
    if (key === 'idle-timeout') parseDuration(value);
    if (key === 'kind' && !['node', 'chrome', 'hermes'].includes(value)) throw new DbgError('BAD_ARGS', '--kind must be node, chrome or hermes');
    if (key === 'target') { try { new RegExp(value); } catch { throw new DbgError('BAD_ARGS', '--target must be a valid regular expression'); } }
  }
  if (flags.limit !== undefined && (command === 'stack' || command === 'logs')) {
    const cap = command === 'stack' ? 60 : 500;
    if (Number(flags.limit) > cap) throw new DbgError('BAD_ARGS', `${command} --limit must be between 1 and ${cap}`);
  }
  if (!flags.help && (rest.length < spec[0] || rest.length > spec[1])) throw new DbgError('BAD_ARGS', `${command} expects ${spec[0] === spec[1] ? spec[0] : `at least ${spec[0]}`} positional argument(s)`);
  if (command === 'step' && rest.length && !['over', 'into', 'out'].includes(rest[0])) throw new DbgError('BAD_ARGS', 'step expects over, into or out');
  if (command === 'exceptions' && rest.length && !['none', 'uncaught', 'all'].includes(rest[0])) throw new DbgError('BAD_ARGS', 'exceptions expects none, uncaught or all');
  if (command === 'start' && flags.pid && flags.port) throw new DbgError('BAD_ARGS', 'choose --pid or --port');
  if (command === 'start' && flags.pid && flags.kind && flags.kind !== 'node') throw new DbgError('BAD_ARGS', '--pid requires --kind node');
  return { positional, flags };
}
