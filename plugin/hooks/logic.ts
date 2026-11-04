import type { PluginOptions } from 'claude-code'

import type { CheckResult, Problem, ToolItem } from '../types'

/** The kinds the mod may update, each by one fixed argv template. */
export const UPDATABLE = ['plugin', 'skill', 'brew', 'uv'] as const

/** The skills CLI, pinned to the release this mod was tested with (checker/check_updates.py SKILLS_CLI). */
export const SKILLS_CLI = 'skills@1.7.4'

const PLUGIN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}@[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const SKILL_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const BREW_ID = /^[A-Za-z0-9][A-Za-z0-9@._+-]{0,127}(\/[A-Za-z0-9][A-Za-z0-9@._+-]{0,127}){0,2}$/
// A Python package name, as uv tool list prints it: no version, extra or path.
const UV_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

/**
 * The one argv an item may run, or undefined. The id comes from a command's output (a trust
 * boundary), so it must match its kind's pattern: no leading dash, no shell, no path tricks.
 */
export function argvFor(item: Pick<ToolItem, 'id' | 'kind' | 'cask'>): string[] | undefined {
  const { id, kind } = item
  if (kind === 'plugin' && PLUGIN_ID.test(id)) return ['claude', 'plugin', 'update', id]
  if (kind === 'skill' && SKILL_ID.test(id)) return ['npx', '--yes', SKILLS_CLI, 'update', '-g', id]
  if (kind === 'brew' && BREW_ID.test(id)) return ['brew', 'upgrade', ...(item.cask === true ? ['--cask'] : []), id]
  if (kind === 'uv' && UV_ID.test(id)) return ['uv', 'tool', 'upgrade', id]
  return undefined
}

/** An item's identity: a skill and a formula, or a formula and a cask, may share a name. */
export const keyOf = (item: Pick<ToolItem, 'id' | 'kind' | 'cask'>): string => `${item.kind}:${item.id}${item.cask === true ? ':cask' : ''}`

/** Set over the host's environment for each kind's update. */
export const UPDATE_ENV: Partial<Record<string, Record<string, string>>> = {
  // The skills CLI reports each update to its telemetry unless told not to.
  skill: { DISABLE_TELEMETRY: '1', DO_NOT_TRACK: '1' },
  // Homebrew then runs `sudo -A`, which fails at once: a cask that wants a password must not wait on a prompt no one sees.
  brew: { SUDO_ASKPASS: '/usr/bin/false' },
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

// Terminal escape sequences (CSI, OSC) whole, so no fragment of one reaches the drawing.
const ESCAPES = /\u001b\[[0-9;?]*[ -\/]*[@-~]|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)?/g

/** Outside text as one safe line: no escape sequences or control characters, bounded. */
export const clean = (v: unknown, max = 300): string =>
  typeof v === 'string' || typeof v === 'number'
    ? String(v).replace(ESCAPES, '').replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ').slice(0, max)
    : ''

/** Outside text kept as lines (a command's output), otherwise as `clean`; the last `max` characters. */
export const cleanLines = (text: string, max: number): string =>
  text.replace(ESCAPES, '').replace(/\r\n?/g, '\n').replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, ' ').slice(-max)

/** The checker's JSON, or null when the text is not that shape. Unknown fields are dropped. */
export function parseResult(text: string): CheckResult | null {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return null
  }
  if (!isObject(data) || !Array.isArray(data.items)) return null
  const lines = (v: unknown) => (Array.isArray(v) ? v.filter(s => typeof s === 'string').map(s => clean(s)) : [])
  const items: ToolItem[] = []
  for (const i of data.items) {
    if (!isObject(i) || typeof i.id !== 'string' || typeof i.kind !== 'string') continue
    items.push({
      id: i.id.slice(0, 300),
      kind: clean(i.kind, 20),
      name: clean(i.name) || clean(i.id),
      have: clean(i.have, 80),
      latest: clean(i.latest, 80),
      ...(i.note ? { note: clean(i.note) } : {}),
      ...(i.detail ? { detail: clean(i.detail, 600) } : {}),
      ...(i.cask === true ? { cask: true } : {}),
    })
  }
  const problems: Problem[] = []
  for (const p of Array.isArray(data.problems) ? data.problems : []) {
    if (typeof p === 'string') problems.push({ text: clean(p) })
    else if (isObject(p) && typeof p.text === 'string')
      problems.push({ text: clean(p.text), ...(p.hint ? { hint: clean(p.hint) } : {}), ...(p.offline === true ? { offline: true } : {}) })
  }
  return {
    checkedAt: clean(data.checkedAt, 40),
    checked: lines(data.checked),
    skipped: lines(data.skipped),
    items,
    problems,
  }
}

export function parseKinds(option: PluginOptions[string] | undefined): string[] {
  const listed = typeof option === 'string' ? option.split(',').map(k => k.trim()) : []
  return UPDATABLE.filter(k => listed.includes(k))
}

export type Sorted = { updatable: ToolItem[]; info: ToolItem[]; broken: ToolItem[]; problems: Problem[] }

/** Splits a result into what the pane draws; an item with an id that is not valid becomes a problem row. */
export function sortItems(result: CheckResult | null, kinds: readonly string[]): Sorted {
  const out: Sorted = { updatable: [], info: [], broken: [], problems: [...(result?.problems ?? [])] }
  for (const item of result?.items ?? []) {
    if (item.kind === 'broken') out.broken.push(item)
    else if (item.kind === 'self' || item.kind === 'info') out.info.push(item)
    else if (!kinds.includes(item.kind)) continue
    else if (argvFor(item)) out.updatable.push(item)
    else out.problems.push({ text: `${item.name}: not a valid ${item.kind} id, not offered for update` })
  }
  // Grouped in display order (Plugins, Skills, Homebrew, uv tools); Update all runs in this order too.
  out.updatable.sort((a, b) => kinds.indexOf(a.kind) - kinds.indexOf(b.kind))
  return out
}

/** Which updates the band was hidden for: it shows again when this changes. */
export const signature = (items: readonly ToolItem[]): string =>
  items.map(i => `${keyOf(i)}@${i.latest}`).sort().join('|')

export const hasOffline = (problems: readonly Problem[]): boolean => problems.some(p => p.offline === true)

/** What to tell after a kind ran: confirm-one, confirm-all and the result's Next line; skills say one thing. */
export const AFTER = {
  plugin: { one: 'run /reload-plugins to load it in this session', all: 'run /reload-plugins to load the plugin updates', next: 'run /reload-plugins to load the plugin update.' },
  skill: 'updated skills load in the next session',
} as const

export function checkerArgv(root: string, cachePath: string | undefined, isFetch: boolean, options: PluginOptions, isWindows = false): string[] {
  const opt = (key: string) => {
    const v = options[key]
    return typeof v === 'string' ? v.trim() : ''
  }
  // `--flag=value` so a value that starts with a dash is never read as a flag.
  // Windows: the py launcher, as `python3` there may be the Store alias that opens the Store.
  // UTF-8 mode, so the files and the tools' output are not read in the Windows code page.
  return [
    ...(isWindows ? ['py', '-3'] : ['python3']),
    '-I',
    '-X',
    'utf8',
    `${root}/checker/check_updates.py`,
    '--json',
    ...(cachePath ? [`--cache=${cachePath}`] : []),
    ...(isFetch ? ['--fetch'] : []),
    ...(opt('brewfile') ? [`--brewfile=${opt('brewfile')}`] : []),
    ...(opt('knownBroken') ? [`--known-broken=${opt('knownBroken')}`] : []),
    ...(opt('checkCommand') ? [`--extra=${opt('checkCommand')}`] : []),
    ...(typeof options.kinds === 'string' ? [`--kinds=${opt('kinds')}`] : []),
  ]
}

export type CheckFailure = 'start' | 'timeout' | 'output'

/** What to do about a check that failed, by how it failed and on which system. */
export function checkHint(failure: CheckFailure, isWindows: boolean, root: string): string {
  if (failure === 'timeout') return 'a tool or the network hung: press r to check again, or narrow kinds in /config'
  if (failure === 'start')
    return isWindows
      ? 'install Python 3 from python.org, with its py launcher'
      : 'install Python 3 (macOS: xcode-select --install or brew install python; Linux: the python3 package)'
  return `run it in a terminal to see the whole error: ${isWindows ? 'py -3' : 'python3'} -I -X utf8 ${root}/checker/check_updates.py`
}

// What a Python that is missing says when its launcher or stub starts: the macOS stub without the developer
// tools, and Windows' py launcher with no Python 3 installed.
export const NO_PYTHON = /xcrun: error|developer tools|No suitable Python runtime|Python was not found/i

/**
 * The argv as the host starts it. On Windows `npx`, and `claude` when npm installed it, are .cmd shims,
 * which only cmd.exe starts (a native claude.exe starts through it too); the plugin and skill id patterns
 * admit none of cmd's special characters.
 * Drop cmd.exe once $.process.run starts .cmd shims itself.
 */
export const onHost = (argv: readonly string[], isWindows: boolean): string[] =>
  isWindows && (argv[0] === 'npx' || argv[0] === 'claude') ? ['cmd.exe', '/d', '/c', ...argv] : [...argv]

/** `m:ss` */
export function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** The check's local time as `09:12`, or `10-19 09:12` when it was not today (in the check's own time zone). */
export function when(iso: string, nowMs: number): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/.exec(iso)
  if (!m) return iso || 'unknown'
  const [, day = '', time = '', zone = 'Z'] = m
  const offset = zone === 'Z' ? 0 : (zone.startsWith('-') ? -1 : 1) * (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4, 6)))
  const today = new Date(nowMs + offset * 60_000).toISOString().slice(0, 10)
  return day === today ? time : `${day.slice(5)} ${time}`
}

/** Pads or cuts to exactly `width` cells (an ellipsis marks a cut). */
export function fit(text: string, width: number): string {
  if (width <= 0) return ''
  return text.length > width ? `${text.slice(0, width - 1)}…` : text.padEnd(width)
}

/** The last non-empty line of a command's output, as one safe line. */
export const lastLine = (text: string): string =>
  clean(
    cleanLines(text, 100_000)
      .split('\n')
      .map(l => l.trim())
      .filter(Boolean)
      .at(-1) ?? '',
  )

const versions = (i: ToolItem) => `${i.have} → ${i.latest}`

/** The plain-text report: the command's reply everywhere, and the whole UX under `claude -p`. */
export function report(result: CheckResult | null, kinds: readonly string[], isHeadless: boolean, nowMs: number): string {
  if (!result) return 'Uptick: not checked yet'
  const { updatable, info, broken, problems } = sortItems(result, kinds)
  const status = updatable.length ? `${updatable.length} available` : 'all current'
  const lines = [`Uptick: ${status} (checked ${when(result.checkedAt, nowMs)})`]
  if (!updatable.length && result.checked.length) lines.push(`  ✓ ${result.checked.join(' · ')}`)
  for (const i of updatable) lines.push(`  ↑ ${i.name} ${versions(i)}`, `      ${(argvFor(i) ?? []).join(' ')}`)
  for (const i of info) lines.push(`  · ${i.name} ${versions(i)} (${i.note ?? 'updates itself; new sessions get it'})`)
  for (const i of broken) lines.push(`  ! ${i.name}: ${i.note ?? 'upstream known broken'}`)
  for (const p of problems) lines.push(`  ? ${p.text}${p.hint ? ` (${p.hint})` : ''}`)
  for (const s of result.skipped) lines.push(`  · ${s}`)
  if (isHeadless && updatable.length) lines.push('To update, run /uptick in an interactive session.')
  return lines.join('\n')
}
