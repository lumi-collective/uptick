import { expect, mock, test } from 'claude-code/testing'
import type { Engine, Mounted } from 'claude-code/testing'
import type { On } from 'claude-code'

import { argvFor, checkerArgv, knownBrokenPath, onHost, parseKinds, parseResult, report, SKILLS_CLI, sortItems, withSkip } from './logic.ts'

const PLUGIN = 'uptick'
const HOME = '/home/test'
const WIN_HOME = 'C:\\Users\\test'
const CACHE = `${HOME}/.cache/uptick/last.json`
const NOW = Date.parse('2026-10-21T08:00:00Z')
const SURFACES = ['terminal', 'desktop'] as const

// Synthetic data only.
const RESULT = {
  schema: 1,
  checkedAt: '2026-10-21T09:12:00+02:00',
  fetched: true,
  checked: ['Claude Code 2.1.306', '14 plugins', '31 skills', 'Homebrew'],
  skipped: [],
  items: [
    { id: 'lint-helper@community', kind: 'plugin', name: 'lint-helper@community', have: '1.4.0', latest: '1.5.0' },
    { id: 'code-review', kind: 'skill', name: 'code-review', have: 'a1b2c3d', latest: 'e4f5a6b' },
    { id: 'gh', kind: 'brew', name: 'gh', have: '2.81.0', latest: '2.82.1' },
    { id: 'claude-code', kind: 'self', name: 'Claude Code', have: '2.1.306', latest: '2.1.310' },
    {
      id: 'example-skill',
      kind: 'broken',
      name: 'example-skill',
      have: 'a1b2c3d',
      latest: '0000000',
      note: 'upstream known broken, keeping installed',
      detail: 'since 2026-01-01: SKILL.md has invalid YAML front matter',
    },
  ],
  problems: [{ text: '2 skills not checked: GitHub not reachable', hint: 'run gh auth login' }],
}

const UPDATE_PREFIXES = [
  ['claude', 'plugin', 'update'],
  ['npx', '--yes', SKILLS_CLI, 'update'],
  ['brew', 'upgrade'],
  ['uv', 'tool', 'upgrade'],
]
// Windows starts npx through cmd.exe: the update is what follows it.
const isUpdate = (argv: readonly string[]) => {
  const run = argv[0] === 'cmd.exe' ? argv.slice(3) : argv
  return UPDATE_PREFIXES.some(prefix => prefix.every((word, i) => run[i] === word))
}
const ok = (stdout: string, exitCode = 0) => ({ exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false })

type World = {
  cache: unknown
  allowUpdates: boolean
  ran: string[][]
  cwds: (string | undefined)[]
  inits: ({ env?: Record<string, string>; stdin?: string } | undefined)[]
  opened: number
  openArgs: unknown[]
  isShown: boolean
  registered: string[]
  filled: string[]
  commands: string[]
  updates: () => string[][]
  isEnvGone: boolean
  files: Record<string, string>
  written: { path: string; text: string }[]
  writeDeny: string
  toasts: string[]
}

/** The engine beneath the mod: a cache file, a checker, panes. No real command ever runs. */
function world(
  on: On,
  {
    cache = RESULT as unknown,
    checker = JSON.stringify(RESULT),
    failing = '',
    slow = '',
    isPlaced = true,
    env = { HOME } as Record<string, string>,
    says = {} as Record<string, string>,
    files = {} as Record<string, string>,
  } = {},
) {
  const clock = mock.clock(on, { now: NOW })
  mock.store(on)
  const w: World = { cache, allowUpdates: false, ran: [], cwds: [], inits: [], opened: 0, openArgs: [], isShown: true, registered: [], filled: [], commands: [], updates: () => w.ran.filter(isUpdate), isEnvGone: false, files, written: [], writeDeny: '', toasts: [] }
  on('env.get', (_, e) => (w.isEnvGone ? { deny: 'environment gone' } : { value: env[e.name] }))
  // The engine hands fs.read and fs.write the host's absolute path: on Windows with a drive letter and backslashes.
  const posix = (path: string) => path.replace(/^[A-Za-z]:/, '').replace(/\\/g, '/')
  const isCache = (path: string) => posix(path) === CACHE
  // Other files, by their path in POSIX form: their text, or `deny:<reason>` for one the host refuses.
  on('fs.read', (_, e) => {
    if (w.cache && isCache(e.path)) return { value: JSON.stringify(w.cache) }
    const file = w.files[posix(e.path)]
    if (file === undefined) return { deny: 'ENOENT: no such file' }
    return file.startsWith('deny:') ? { deny: file.slice(5) } : { value: file }
  })
  on('fs.write', (_, e) => {
    if (w.writeDeny) return { deny: w.writeDeny }
    w.written.push({ path: posix(e.path), text: e.text })
    w.files[posix(e.path)] = e.text
    return { value: undefined }
  })
  on('command.register', (_, e) => {
    w.registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('ui.open', (_, e) => {
    w.opened += 1
    w.openArgs.push(e)
    return { value: isPlaced ? { isPlaced: true as const } : { isPlaced: false as const, reason: 'no attached surface places panes' } }
  })
  on('ui.close', () => ({ value: undefined }))
  on('prompt.fill', (_, e) => {
    w.filled.push(e.text)
    return { isFilled: true, text: e.text, cursor: e.text.length }
  })
  // Core's own /reload-plugins, should the mod run it: the test then sees it ran.
  on('command.run', { command: 'reload-plugins' }, () => {
    w.commands.push('reload-plugins')
    return { text: '' }
  })
  on('ui.panes', () => ({ value: [{ id: 'uptick', title: 'Uptick', isShown: w.isShown, isFocused: w.isShown, isPlaced }] }))
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', (_, e) => {
    w.toasts.push(e.text)
    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('process.run', async (_, e) => {
    const argv = [...e.argv]
    w.ran.push(argv)
    w.cwds.push(e.init?.cwd)
    w.inits.push(e.init)
    if (argv[0] === 'python3' || argv[0] === 'py') {
      if (slow === 'checker') await clock.sleep(3000)
      return checker.startsWith('deny:') ? { deny: checker.slice(5) } : { value: ok(checker) }
    }
    // The proof that nothing installs unasked: an update argv before a confirm fails the test.
    expect(isUpdate(argv) && w.allowUpdates, `ran without a confirm: ${argv.join(' ')}`).toBe(true)
    if (argv.at(-1) === slow) await clock.sleep(5000)
    if (argv.at(-1) === failing) return { value: { ...ok(''), exitCode: 1, stderr: '==> Upgrading\n\u001b[31mError:\u001b[0m Cannot write to the Cellar\n' } }
    return { value: ok(says[argv.at(-1) ?? ''] ?? `updated ${argv.at(-1)}`) }
  })
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  return { clock, w }
}

const start = ($: Engine, isInteractive = true) =>
  $.session.start({ cwd: '/', surface: isInteractive ? 'terminal' : null, isInteractive })

const runCommand = ($: Engine) =>
  $.command.run({ command: 'uptick', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 140 } })

const pane = (bodyColumns: number) => ({
  plugin: PLUGIN,
  component: 'Pane' as const,
  requestId: 'uptick',
  props: { title: 'Uptick', isFocused: true, bodyColumns, placement: 'dock' as const, scroll: { offset: 0, bodyRows: 40 }, view: {} },
})

const band = (overrides: { hasSurvey?: boolean; isWorking?: boolean; bodyColumns?: number } = {}) => ({
  plugin: PLUGIN,
  component: 'AbovePrompt' as const,
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {}, ...overrides },
})

test('parses the checker JSON into rows, and runs the checker as argv', async ($, on) => {
  const { clock, w } = world(on, { cache: null })
  await start($)
  const reply = await runCommand($)
  expect(reply.text).toBe('Uptick: not checked yet; checking now, the list is in the pane')
  await clock.settle()
  expect(w.opened).toBe(1)
  // The session start's check, with no result yet: the first fetch of the day; the open joins it.
  expect(w.ran).toEqual([
    [
      'python3',
      '-I',
      '-X',
      'utf8',
      expect.stringMatching(/\/checker\/check_updates\.py$/),
      '--json',
      `--cache=${CACHE}`,
      '--fetch',
      '--kinds=plugin,skill,brew',
    ],
  ])
  expect(w.cwds[0]).toBe(HOME)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...pane(68), surface })
    expect((await ui.find({ type: 'Text', text: /Uptick/ }))?.text).toBe('Uptick · 3 available · checked 09:12')
    expect(await ui.findAll({ type: 'Button', key: 'item:brew:gh' })).toHaveLength(1)
    expect((await ui.find({ key: 'item:plugin:lint-helper@community' }))?.props.hotkey).toBe('1')
    expect((await ui.find({ type: 'Text', text: /Claude Code/ }))?.props.dimColor).toBe(true)
    expect((await ui.find({ type: 'Text', text: /2 skills not checked/ }))?.text).toContain('(run gh auth login)')
    expect((await ui.find({ key: 'update-all' }))?.props.label).toBe('Update all (3)')
    await ui.unmount()
  }
})

test('opening shows the cached list dimmed while it checks, then the fresh one', async ($, on) => {
  const current = { ...RESULT, items: [], problems: [] }
  const { clock } = world(on, { slow: 'checker', checker: JSON.stringify(current) })
  await start($)
  const reply = runCommand($)
  await clock.advance(2000)
  expect((await reply).text).toContain('Uptick: 3 available (checked 09:12)')
  expect((await reply).text).toMatch(/; checking now, the list is in the pane$/) // the count is the last check's
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /Uptick/ }))?.text).toBe('Uptick · checking… 0:02')
  expect((await ui.find({ type: 'Text', text: /no network fetch/ }))?.text).toBe('  plugins, skills, Homebrew · no network fetch')
  expect((await ui.find({ type: 'Text', text: /last check/ }))?.text).toBe('  last check 09:12')
  expect((await ui.find({ type: 'Text', text: /lint-helper/ }))?.props.dimColor).toBe(true)
  expect(await ui.find({ key: 'item:plugin:lint-helper@community' })).toBeUndefined()
  await clock.advance(1000)
  expect((await ui.find({ type: 'Text', text: /Uptick/ }))?.text).toMatch(/^Uptick · all current · checked/)
  expect((await ui.find({ type: 'Text', text: /✓/ }))?.text).toBe('  ✓ Claude Code 2.1.306 · 14 plugins · 31 skills · Homebrew')
  expect((await ui.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['check', 'close'])
  await ui.unmount()
})

test('a checker that prints no JSON is the check-failed state', async ($, on) => {
  const { clock } = world(on, { cache: null, checker: 'Traceback (most recent call last)' })
  await start($)
  await runCommand($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /Uptick/ }))?.text).toBe('Uptick · check failed')
  expect((await ui.find({ type: 'Text', text: /✗/ }))?.text).toBe('  ✗ checker exited 0: no JSON output')
  expect((await ui.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['check', 'close'])
  await ui.unmount()
})

test('parseResult keeps only the schema and strips control characters', async () => {
  expect(parseResult('not json')).toBe(null)
  expect(parseResult('{"items": "no"}')).toBe(null)
  const parsed = parseResult(
    JSON.stringify({ checkedAt: 'x', items: [{ id: 'a', kind: 'skill', name: 'a\u001b[31mred', have: 1, latest: '2' }, { kind: 'skill' }], problems: ['p', 7] }),
  )
  expect(parsed?.items).toEqual([{ id: 'a', kind: 'skill', name: 'ared', have: '1', latest: '2' }])
  expect(parsed?.problems).toEqual([{ text: 'p' }])
  expect(parsed?.checked).toEqual([])
})

test('a known-broken upstream is a note: no button, not in Update all', async ($, on) => {
  world(on)
  await start($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...pane(68), surface })
    expect(await ui.find({ key: 'item:broken:example-skill' })).toBeUndefined()
    const row = await ui.find({ type: 'Text', text: /example-skill/ })
    expect(row?.text).toMatch(/^ {3}! example-skill +a1b2c3d → 0000000 +held back$/)
    expect((await ui.findAll({ type: 'Text' })).find(t => t.text === '!')?.props.color).toBe('warning')
    expect((await ui.find({ type: 'Text', text: /since 2026-01-01/ }))?.props.italic).toBe(true)
    await ui.press({ key: 'update-all' })
    expect((await ui.find({ type: 'Text', text: /^Update 3 items\?/ }))?.props).toMatchObject({ weight: 'bold' })
    expect((await ui.find({ type: 'Text', text: /^Update 3 items\?/ }))?.props.color).toBeUndefined()
    expect((await ui.find({ key: 'confirm' }))?.props.variant).toBe('primary')
    expect((await ui.findAll({ type: 'Text', text: /^ {2}\d / })).map(t => t.text)).toEqual([
      '  1 claude plugin update lint-helper@community',
      `  2 npx --yes ${SKILLS_CLI} update -g code-review`,
      '  3 brew upgrade gh',
    ])
    expect((await ui.find({ type: 'Text', text: /skips/ }))?.text).toBe('  skips  example-skill (known issue), Claude Code (info)')
    await ui.press({ key: 'cancel' })
    await ui.unmount()
  }
  expect(sortItems(parseResult(JSON.stringify(RESULT)), ['plugin', 'skill', 'brew']).broken.map(i => i.id)).toEqual(['example-skill'])
})

test('nothing updates without the confirm press; the confirm runs exactly one argv', async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'item:plugin:lint-helper@community' })
  expect((await ui.find({ type: 'Text', text: /^Update lint-helper/ }))?.text).toBe('Update lint-helper@community 1.4.0 → 1.5.0?')
  expect((await ui.find({ type: 'Text', text: /runs/ }))?.text).toBe('  runs   claude plugin update lint-helper@community')
  expect((await ui.find({ key: 'cancel' }))?.props.autoFocus).toBe(true)
  expect((await ui.find({ key: 'confirm' }))?.props.autoFocus).toBeUndefined()
  expect(w.updates()).toEqual([])
  await ui.press({ key: 'cancel' })
  expect(await ui.find({ key: 'confirm' })).toBeUndefined()
  expect(await ui.find({ key: 'item:brew:gh' })).toBeDefined()
  expect(w.updates()).toEqual([])

  await ui.press({ key: 'item:plugin:lint-helper@community' })
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  expect(w.updates()).toEqual([['claude', 'plugin', 'update', 'lint-helper@community']])
  expect(w.cwds.at(w.ran.findIndex(isUpdate))).toBe(HOME)
  expect((await ui.find({ type: 'Text', text: /^Done/ }))?.text).toMatch(/^Done · 1 updated · \d+:\d\d$/)
  expect((await ui.find({ type: 'Text', text: /lint-helper/ }))?.text).toContain('✓ lint-helper@community')
  expect((await ui.find({ type: 'Text', text: /^Next:/ }))?.text).toBe('Next: run /reload-plugins to load the plugin update.')
  expect(await ui.find({ key: 'reload' })).toBeDefined()
  // Reload plugins only types the command into the prompt; the person presses Enter.
  await ui.press({ key: 'reload' })
  expect(w.filled).toEqual(['/reload-plugins'])
  expect(w.commands).toEqual([])
  await ui.unmount()
})

const KNOWN_BROKEN = `${HOME}/.config/uptick/known-broken.json`
const checks = (w: World) => w.ran.filter(argv => argv[0] === 'python3').length

test('Skip holds that version back: an entry in the known-broken list, then a check', async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  const before = checks(w)
  await ui.press({ key: 'item:brew:gh' })
  const skip = await ui.find({ key: 'skip' })
  expect(skip?.props.label).toBe('Skip 2.82.1')
  expect(skip?.props.hotkey).toBeUndefined() // as with Update, a typed letter must not change a file
  await ui.press({ key: 'skip' })
  await clock.settle()
  expect(w.written.map(f => f.path)).toEqual([KNOWN_BROKEN])
  // exact: the checker holds back 2.82.1 only, never a later 2.82.1.x or 2.82.1_1.
  expect(JSON.parse(w.files[KNOWN_BROKEN] ?? '')).toEqual({ gh: { match: '2.82.1', kind: 'brew', exact: true, note: 'skipped in the Uptick pane', since: '2026-10-21' } })
  expect(w.toasts).toEqual(['Uptick: gh 2.82.1 skipped until a newer version'])
  expect(checks(w)).toBe(before + 1)
  expect(w.ran.at(-1)).not.toContain('--fetch')
  expect(await ui.find({ key: 'confirm' })).toBeUndefined()
  expect(w.updates()).toEqual([])
  await ui.unmount()
})

test('Skip keeps the entries in the list and writes where the setting points', { options: { knownBroken: ' ~/notes/kb.json ' } }, async ($, on) => {
  const path = `${HOME}/notes/kb.json`
  const { clock, w } = world(on, { files: { [path]: '{"other-tool": {"match": "1.0", "note": "mine"}}' } })
  await start($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'item:skill:code-review' })
  await ui.press({ key: 'skip' })
  await clock.settle()
  expect(w.written.map(f => f.path)).toEqual([path])
  expect(JSON.parse(w.files[path] ?? '')).toEqual({
    'other-tool': { match: '1.0', note: 'mine' },
    'code-review': { match: 'e4f5a6b', kind: 'skill', exact: true, note: 'skipped in the Uptick pane', since: '2026-10-21' },
  })
  await ui.unmount()
})

test('Skip never writes over a list it cannot read, nor over an entry already there', async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  const cases: [string, string][] = [
    ['{"gh": {"match": "2.0", "note": "mine"}}', 'it already has an entry for gh'],
    ['deny:EACCES: permission denied', 'EACCES: permission denied'],
    ['{ "gh": ', 'it is not valid JSON'],
    ['["gh"]', 'it is not a JSON object'],
  ]
  for (const [file, why] of cases) {
    w.files[KNOWN_BROKEN] = file
    w.toasts.length = 0
    await ui.press({ key: 'item:brew:gh' })
    await ui.press({ key: 'skip' })
    await clock.settle()
    expect(w.written, file).toEqual([])
    expect(w.files[KNOWN_BROKEN], file).toBe(file)
    expect(w.toasts, file).toHaveLength(1)
    expect(w.toasts[0], file).toContain(`Uptick: gh not skipped: ${KNOWN_BROKEN}: `)
    expect(w.toasts[0], file).toContain(why)
    if (await ui.find({ key: 'cancel' })) await ui.press({ key: 'cancel' })
  }
  // A list that reads but cannot be written: a message, the list as it was, and no check.
  w.files[KNOWN_BROKEN] = '{}'
  w.writeDeny = 'EROFS: read-only file system'
  w.toasts.length = 0
  const before = checks(w)
  await ui.press({ key: 'item:brew:gh' })
  await ui.press({ key: 'skip' })
  await clock.settle()
  expect(w.files[KNOWN_BROKEN]).toBe('{}')
  expect(w.toasts).toHaveLength(1)
  expect(w.toasts[0]).toContain(`Uptick: gh not skipped: ${KNOWN_BROKEN}: could not be written (`)
  expect(w.toasts[0]).toContain('EROFS')
  expect(checks(w)).toBe(before)
  await ui.unmount()
})

test('on Windows Skip writes under USERPROFILE, where the checker looks, also with HOME set', async ($, on) => {
  const { clock, w } = world(on, { env: { HOME: '/elsewhere', USERPROFILE: WIN_HOME, OS: 'Windows_NT' } })
  await start($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'item:brew:gh' })
  await ui.press({ key: 'skip' })
  await clock.settle()
  // The world keeps the host's path in POSIX form: on Windows the drive letter goes, on a Mac host the Windows path
  // lands under the cwd. Either way it ends in USERPROFILE's folder, not HOME's.
  expect(w.written).toHaveLength(1)
  expect(w.written[0]?.path.endsWith('/Users/test/.config/uptick/known-broken.json')).toBe(true)
  await ui.unmount()
})

test('withSkip replaces only its own entry; knownBrokenPath names the file the checker reads', async () => {
  const item = { id: 'gh', kind: 'brew', latest: '2.83.0' }
  const own = JSON.stringify({ gh: { match: '2.82.1', kind: 'brew', note: 'skipped in the Uptick pane', since: '2026-10-01' } })
  const again = withSkip(own, item, '2026-10-21')
  expect('text' in again ? JSON.parse(again.text) : again).toEqual({ gh: { match: '2.83.0', kind: 'brew', exact: true, note: 'skipped in the Uptick pane', since: '2026-10-21' } })
  expect(withSkip('{"gh": {"match": "2.0", "note": "mine"}}', item, '2026-10-21')).toEqual({ refused: 'it already has an entry for gh' })
  expect(withSkip('{"gh": "2.0"}', item, '2026-10-21')).toEqual({ refused: 'it already has an entry for gh' })
  // Skip's own entry for the skill named gh is still a hold: the brew gh may not replace it.
  const skill = JSON.stringify({ gh: { match: 'e4f5a6b', kind: 'skill', note: 'skipped in the Uptick pane', since: '2026-10-01' } })
  expect(withSkip(skill, item, '2026-10-21')).toEqual({ refused: 'it already has an entry for gh' })
  const set = (knownBroken: string) => ({ knownBroken })
  expect(knownBrokenPath({}, '/xdg', HOME)).toBe('/xdg/uptick/known-broken.json')
  expect(knownBrokenPath({}, undefined, HOME)).toBe(`${HOME}/.config/uptick/known-broken.json`)
  expect(knownBrokenPath({}, undefined, undefined)).toBeUndefined()
  expect(knownBrokenPath(set('/etc/kb.json'), undefined, undefined)).toBe('/etc/kb.json')
  expect(knownBrokenPath(set('D:\\lists\\kb.json'), undefined, WIN_HOME)).toBe('D:\\lists\\kb.json')
  expect(knownBrokenPath(set('~\\kb.json'), undefined, WIN_HOME)).toBe(`${WIN_HOME}\\kb.json`)
  expect(knownBrokenPath(set('kb.json'), undefined, HOME)).toBe(`${HOME}/kb.json`) // the checker runs from home
  // Windows with HOME set: `~` is USERPROFILE for Python, a relative path is under the folder the checker runs from.
  expect(knownBrokenPath(set('kb.json'), undefined, WIN_HOME, '/elsewhere')).toBe('/elsewhere/kb.json')
  expect(knownBrokenPath(set('~\\kb.json'), undefined, WIN_HOME, '/elsewhere')).toBe(`${WIN_HOME}\\kb.json`)
  expect(knownBrokenPath({}, undefined, WIN_HOME, '/elsewhere')).toBe(`${WIN_HOME}/.config/uptick/known-broken.json`)
  expect(knownBrokenPath(set('~other/kb.json'), undefined, HOME)).toBeUndefined()
  expect(knownBrokenPath(set('~/kb.json'), undefined, undefined)).toBeUndefined()
})

/** Types into a pane that holds the keys: each character presses the drawn Button whose hotkey it is, as the engine does. */
async function typeInto(ui: Pick<Mounted, 'findAll' | 'press'>, text: string) {
  for (const char of text) {
    const button = (await ui.findAll({ type: 'Button' })).find(b => b.props.hotkey === char)
    if (button?.key) await ui.press({ key: button.key })
  }
}

test('text typed into the focused pane never runs an update: no letter confirms', async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...pane(68), surface })
    for (const text of ['okay', 'naïve café, déjà vu', '1 sorry, go', '3 day yes', 'any key, yay']) {
      await typeInto(ui, text)
      await clock.advance(2000)
      await typeInto(ui, 'yyyy')
      expect(w.updates(), text).toEqual([])
      expect(w.written, text).toEqual([]) // nor skips a version
      if (await ui.find({ key: 'cancel' })) await ui.press({ key: 'cancel' })
    }
    await typeInto(ui, 'a')
    const confirm = await ui.find({ key: 'confirm' })
    expect(confirm?.props.hotkey).toBeUndefined()
    expect(confirm?.props.plain).toBeUndefined() // drawn [ Update 3 ]: a control reached by Tab or the arrows
    expect((await ui.find({ key: 'cancel' }))?.props).toMatchObject({ hotkey: 'n', autoFocus: true })
    expect((await ui.find({ type: 'Text', text: /Tab or arrows/ }))?.text).toBe('Tab or arrows to Update, then Enter')
    // Activating the Button itself (Tab, then Enter, or a click) is the one way to run it.
    w.allowUpdates = true
    await ui.press({ key: 'confirm' })
    expect(w.updates()).toHaveLength(3)
    w.allowUpdates = false
    w.ran.length = 0
    await ui.press({ key: 'close' })
    await ui.unmount()
  }
})

test('a reload in the middle of a check leaves no check waiting forever', async ($, on) => {
  const { clock } = world(on, { slow: 'checker' })
  await start($)
  void runCommand($)
  await clock.advance(1000)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /Uptick/ }))?.text).toBe('Uptick · checking… 0:01')
  await start($) // what a reload runs again; the old module's check is gone with it
  expect((await ui.find({ type: 'Text', text: /Uptick/ }))?.text).toBe('Uptick · 3 available · checked 09:12')
  expect(await ui.find({ key: 'check' })).toBeDefined()
  await ui.unmount()
})

test('a failed update shows its last error line, a retry goes through confirm again', async ($, on) => {
  const { clock, w } = world(on, { failing: 'gh' })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'item:brew:gh' })
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  expect((await ui.find({ type: 'Text', text: /exit 1/ }))?.text).toContain('✗ gh')
  expect((await ui.find({ type: 'Text', text: /Cannot write/ }))?.text).toBe('      Error: Cannot write to the Cellar')
  await ui.press({ key: 'output' })
  expect((await ui.find({ type: 'Code' }))?.props.source).toBe('$ brew upgrade gh\n==> Upgrading\nError: Cannot write to the Cellar')
  await ui.press({ key: 'back' })
  const before = w.updates().length
  await ui.press({ key: 'retry:brew:gh' })
  expect(await ui.find({ key: 'confirm' })).toBeDefined()
  expect(w.updates()).toHaveLength(before)
  await ui.unmount()
})

test('running: a progress list with the elapsed time; Stop skips what is still queued', async ($, on) => {
  const { clock, w } = world(on, { slow: 'lint-helper@community' })
  await start($)
  for (const surface of SURFACES) {
    w.ran.length = 0
    const ui = await $.ui.mount({ ...pane(68), surface })
    await ui.press({ key: 'update-all' })
    await clock.advance(1000)
    w.allowUpdates = true
    const pressed = ui.press({ key: 'confirm' })
    await clock.advance(2000)
    expect((await ui.find({ type: 'Text', text: /^Updating/ }))?.text).toBe('Updating 1 of 3 · lint-helper@community · 0:02')
    expect((await ui.find({ type: 'Text', text: /code-review/ }))?.text).toBe('  · code-review            queued')
    expect((await runCommand($)).text).not.toContain('checking now') // no check starts during a run
    expect(await ui.find({ key: 'close' })).toBeUndefined()
    await ui.press({ key: 'stop' })
    expect((await ui.find({ type: 'Text', text: /stops after/ }))?.text).toBe('stops after this item')
    await clock.advance(5000)
    await pressed
    expect((await ui.find({ type: 'Text', text: /^Done/ }))?.text).toBe('Done · 1 updated · 2 skipped · 0:05')
    expect((await ui.find({ type: 'Text', text: /gh/ }))?.text).toContain('· gh')
    expect((await ui.find({ type: 'Text', text: /gh/ }))?.text).toContain('skipped (stopped)')
    expect(w.updates()).toEqual([['claude', 'plugin', 'update', 'lint-helper@community']])
    w.allowUpdates = false
    await ui.press({ key: 'close' })
    await ui.unmount()
  }
})

test('ids are checked before they reach argv', async ($, on) => {
  expect(argvFor({ id: 'name@market', kind: 'plugin' })).toEqual(['claude', 'plugin', 'update', 'name@market'])
  expect(argvFor({ id: 'notes-helper.v2', kind: 'skill' })).toEqual(['npx', '--yes', SKILLS_CLI, 'update', '-g', 'notes-helper.v2'])
  expect(SKILLS_CLI).toMatch(/^skills@\d+\.\d+\.\d+$/) // pinned: an exact version, never the latest
  expect(argvFor({ id: 'python@3.13', kind: 'brew' })).toEqual(['brew', 'upgrade', 'python@3.13'])
  expect(argvFor({ id: 'example/tap/widget', kind: 'brew', cask: true })).toEqual(['brew', 'upgrade', '--cask', 'example/tap/widget'])
  expect(argvFor({ id: 'example_tool.cli-2', kind: 'uv' })).toEqual(['uv', 'tool', 'upgrade', 'example_tool.cli-2'])
  for (const [id, kind] of [
    ['--reinstall', 'uv'],
    ['a b', 'uv'],
    ['a;b', 'uv'],
    ['../a', 'uv'],
    ['a/b', 'uv'],
    ['a@1.0', 'uv'],
    ['a==1.0', 'uv'],
    ['x'.repeat(200), 'uv'],
    ['x; rm -rf ~', 'skill'],
    ['--force', 'brew'],
    ['-y', 'skill'],
    ['../y', 'brew'],
    ['a/../b', 'brew'],
    ['name', 'plugin'],
    ['a@b@c', 'plugin'],
    ['$(id)', 'skill'],
    ['gh\nrm', 'brew'],
    ['gh', '__proto__'],
    ['gh', 'self'],
  ]) {
    expect(argvFor({ id: id ?? '', kind: kind ?? '' }), `${kind} ${id}`).toBeUndefined()
  }

  const hostile = { ...RESULT, items: [{ id: '--force', kind: 'brew', name: '--force', have: '1', latest: '2' }, RESULT.items[2]] }
  world(on, { cache: hostile })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect(await ui.findAll({ type: 'Button', key: 'item:brew:--force' })).toEqual([])
  expect((await ui.find({ type: 'Text', text: /not a valid brew id/ }))?.text).toContain('--force: not a valid brew id, not offered for update')
  expect((await ui.find({ key: 'update-all' }))?.props.label).toBe('Update all (1)')
  await ui.unmount()
})

test('the Brewfile option reaches the checker as one argument; unset, none', async ($, on) => {
  expect(checkerArgv('/mod', undefined, false, {}).some(a => a.startsWith('--brewfile'))).toBe(false)
  expect(checkerArgv('/mod', undefined, true, { brewfile: ' ~/dotfiles/Brewfile ', knownBroken: '', checkCommand: 'extra --json' })).toEqual([
    'python3',
    '-I',
    '-X',
    'utf8',
    '/mod/checker/check_updates.py',
    '--json',
    '--fetch',
    '--brewfile=~/dotfiles/Brewfile',
    '--extra=extra --json',
  ])
  expect(checkerArgv('/mod', undefined, false, { kinds: 'skill, brew' }).at(-1)).toBe('--kinds=skill, brew')
})

test('checkerArgv passes an unknown kinds entry on, so the checker can report it', () => {
  expect(checkerArgv('/mod', undefined, false, { kinds: 'plugin,skill;' }).at(-1)).toBe('--kinds=plugin,skill;')
  expect(parseKinds('plugin,skill;')).toEqual(['plugin'])
})

test('XDG_CACHE_HOME moves the cache to $XDG_CACHE_HOME/uptick/last.json', async ($, on) => {
  const { clock, w } = world(on, { cache: null, env: { HOME, XDG_CACHE_HOME: '/xdg/cache' } })
  await start($)
  await runCommand($)
  await clock.settle()
  expect(w.ran[0]).toContain('--cache=/xdg/cache/uptick/last.json')
})

test('Windows: the py launcher, USERPROFILE for home, npx and claude through cmd.exe', async ($, on) => {
  const { clock, w } = world(on, { cache: null, env: { USERPROFILE: WIN_HOME, OS: 'Windows_NT' } })
  await start($)
  await runCommand($)
  await clock.settle()
  expect(w.ran[0]?.slice(0, 5)).toEqual(['py', '-3', '-I', '-X', 'utf8'])
  expect(w.ran[0]).toContain(`--cache=${WIN_HOME}/.cache/uptick/last.json`)
  expect(w.cwds[0]).toBe(WIN_HOME)
  // What the confirm step shows is what runs, cmd.exe included; the reply is one line (claude -p's report: below).
  expect((await runCommand($)).text).not.toContain('\n')
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'update-all' })
  expect((await ui.findAll({ type: 'Text', text: /^ {2}\d / })).map(t => t.text)).toEqual([
    '  1 cmd.exe /d /c claude plugin update lint-helper@community',
    `  2 cmd.exe /d /c npx --yes ${SKILLS_CLI} update -g code-review`,
    '  3 brew upgrade gh',
  ])
  // cmd.exe makes these lines longer than the 64-column pane: they wrap, so the id is never cut off
  expect((await ui.findAll({ type: 'Text', text: /^ {2}\d / })).map(t => t.props.wrap)).toEqual(['wrap', 'wrap', 'wrap'])
  await ui.press({ key: 'cancel' })
  await ui.press({ key: 'item:skill:code-review' })
  expect((await ui.find({ type: 'Text', text: /runs/ }))?.text).toBe(`  runs   cmd.exe /d /c npx --yes ${SKILLS_CLI} update -g code-review`)
  expect((await ui.find({ type: 'Text', text: /runs/ }))?.props.wrap).toBe('wrap')
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  expect(w.updates()).toEqual([['cmd.exe', '/d', '/c', 'npx', '--yes', SKILLS_CLI, 'update', '-g', 'code-review']])
  expect(w.cwds.at(w.ran.findIndex(isUpdate))).toBe(WIN_HOME)
  await ui.unmount()
  // An npm-installed claude is a .cmd shim too; on other systems the argv runs as it is.
  expect(onHost(['claude', 'plugin', 'update', 'a@b'], true)).toEqual(['cmd.exe', '/d', '/c', 'claude', 'plugin', 'update', 'a@b'])
  expect(onHost(['claude', 'plugin', 'update', 'a@b'], false)).toEqual(['claude', 'plugin', 'update', 'a@b'])
  expect(onHost(['npx', '--yes'], false)).toEqual(['npx', '--yes'])
  // What reaches cmd.exe is an id the pattern admits: none of cmd's special characters.
  for (const id of ['a&b@m', 'a|b@m', 'a^b@m', 'a%PATH%@m', 'a"b@m', 'a<b@m', 'a>b@m', 'a(b)@m', 'a!b@m', 'a b@m'])
    expect(argvFor({ id, kind: 'plugin' }), id).toBeUndefined()
})

test('the Brewfile option from settings is passed on', { options: { brewfile: '/x/Brewfile' } }, async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  await runCommand($)
  await clock.settle()
  expect(w.ran[0]).toContain('--brewfile=/x/Brewfile')
})

test('claude -p on Windows: the report shows npx and claude through cmd.exe', async ($, on) => {
  world(on, { cache: null, env: { USERPROFILE: WIN_HOME, OS: 'Windows_NT' } })
  await start($, false)
  const reply = await runCommand($)
  expect(reply.text).toContain('\n      cmd.exe /d /c claude plugin update lint-helper@community\n')
  expect(reply.text).toContain(`\n      cmd.exe /d /c npx --yes ${SKILLS_CLI} update -g code-review\n`)
  expect(reply.text).toContain('\n      brew upgrade gh\n')
})

test('claude -p: a read-only text report, nothing opens, nothing updates', async ($, on) => {
  const { w } = world(on, { cache: null })
  await start($, false)
  const reply = await runCommand($)
  expect(reply.text).toBe(
    [
      'Uptick: 3 available (checked 09:12)',
      '  ↑ lint-helper@community 1.4.0 → 1.5.0',
      '      claude plugin update lint-helper@community',
      '  ↑ code-review a1b2c3d → e4f5a6b',
      `      npx --yes ${SKILLS_CLI} update -g code-review`,
      '  ↑ gh 2.81.0 → 2.82.1',
      '      brew upgrade gh',
      '  · Claude Code 2.1.306 → 2.1.310 (updates itself; new sessions get it)',
      '  ! example-skill: upstream known broken, keeping installed',
      '  ? 2 skills not checked: GitHub not reachable (run gh auth login)',
      'To update, run /uptick in an interactive session.',
    ].join('\n'),
  )
  expect(reply.exitCode).toBeUndefined()
  expect(w.opened).toBe(0)
  expect(w.updates()).toEqual([])
  expect(w.ran).toHaveLength(1)
})

test('claude -p: a failed check says so and exits 1', async ($, on) => {
  world(on, { cache: null, checker: 'Traceback: boom' })
  await start($, false)
  const reply = await runCommand($)
  expect(reply.text).toMatch(
    /^Uptick: check failed: checker exited 0: no JSON output\n {2}run it in a terminal to see the whole error: python3 -I -X utf8 \S+\/checker\/check_updates\.py$/,
  )
  expect(reply.exitCode).toBe(1)
})

test('claude -p: a checker that runs past its time limit reads as a timeout', async ($, on) => {
  world(on, { cache: null, checker: 'deny:the command timed out after 600000 ms' })
  await start($, false)
  expect((await runCommand($)).text).toBe('Uptick: check failed: check timed out\n  a tool or the network hung: press r to check again, or narrow kinds in /config')
})

test('rows by width: one line from 60, two lines from 40, name and ↑ below', async ($, on) => {
  world(on)
  await start($)
  for (const surface of SURFACES) {
    const wide = await $.ui.mount({ ...pane(68), surface })
    const wideRow = await wide.find({ key: 'item:plugin:lint-helper@community' })
    expect(wideRow?.text).toBe('lint-helper@community  1.4.0 → 1.5.0') // the group header says the kind
    await wide.unmount()

    const narrow = await $.ui.mount({ ...pane(50), surface })
    expect((await narrow.find({ key: 'item:plugin:lint-helper@community', type: 'Button' }))?.text).toBe('lint-helper@community')
    expect((await narrow.find({ type: 'Text', text: /1\.4\.0 → 1\.5\.0/ }))?.text).toBe('     1.4.0 → 1.5.0')
    expect(await narrow.find({ type: 'Text', text: /plugin$/ })).toBeUndefined()
    await narrow.unmount()

    const tiny = await $.ui.mount({ ...pane(35), surface })
    const tinyRow = await tiny.find({ key: 'item:plugin:lint-helper@community' })
    expect(tinyRow?.text).toMatch(/↑$/)
    expect(tinyRow?.text).not.toContain('1.4.0')
    await tiny.unmount()
  }
})

test('the band shows when updates exist, hides until they change, and stays during a turn', async ($, on) => {
  const { w } = world(on)
  await start($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...band(), surface })
    expect((await ui.find({ type: 'Text', text: /tool update/ }))?.text).toBe('↑ 3 tool updates · Plugins 1 · Skills 1 · Homebrew 1 · ? 1')
    expect((await ui.find({ key: 'show' }))?.props.plain).toBe(true)
    expect((await ui.find({ key: 'hide' }))?.props).toMatchObject({ plain: true, dimColor: true })
    await ui.unmount()
  }
  // While Claude works the row stays, above the engine's band, and Show opens the pane mid-turn.
  const working = await $.ui.mount({ ...band({ isWorking: true }), surface: 'terminal' })
  expect((await working.find({ type: 'Text', text: /tool update/ }))?.text).toBe('↑ 3 tool updates · Plugins 1 · Skills 1 · Homebrew 1 · ? 1')
  expect((await working.find({ type: 'Text', text: 'engine' }))?.text).toBe('engine')
  const opened = w.opened
  await working.press({ key: 'show' })
  expect(w.opened).toBe(opened + 1)
  await working.unmount()
  w.isShown = false // the pane is closed again: the band comes back

  const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  await ui.press({ key: 'hide' })
  expect((await ui.find({ type: 'Text' }))?.text).toBe('engine')
  await ui.unmount()
})

test('a newer cache written while the session runs reaches the band within 15 minutes; an older one never', async ($, on) => {
  const { clock, w } = world(on, { cache: { ...RESULT, items: [], problems: [] } })
  await start($)
  const bandText = async () => {
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
    const text = (await ui.find({ type: 'Text' }))?.text
    await ui.unmount()
    return text
  }
  expect(await bandText()).toBe('engine')
  w.cache = { ...RESULT, checkedAt: '2026-10-21T09:50:00+02:00' }
  await clock.advance(899_000)
  expect(await bandText()).toBe('engine')
  await clock.advance(1000)
  expect(await bandText()).toMatch(/^↑ 3 tool updates/)
  w.cache = { ...RESULT, items: [], checkedAt: '2026-10-21T09:30:00+02:00' }
  await clock.advance(900_000)
  expect(await bandText()).toMatch(/^↑ 3 tool updates/)
  expect(w.ran).toHaveLength(0) // re-reading the cache runs nothing
})

test('the band is off when the hint option says so', { options: { hint: 'off' } }, async ($, on) => {
  world(on)
  await start($)
  const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  expect((await ui.find({ type: 'Text' }))?.text).toBe('engine')
  await ui.unmount()
})

test('the report of an all-current result names what was checked', async () => {
  const current = parseResult(JSON.stringify({ ...RESULT, items: [], problems: [] }))
  expect(report(current, ['plugin', 'skill', 'brew'], false, NOW)).toBe(
    'Uptick: all current (checked 09:12)\n  ✓ Claude Code 2.1.306 · 14 plugins · 31 skills · Homebrew',
  )
  expect(report(current, ['plugin'], false, NOW + 86_400_000)).toMatch(/^Uptick: all current \(checked 10-21 09:12\)/)
})

// A fuller result, listed out of display order, plus a plugin that could not be compared.
const FULL = {
  ...RESULT,
  items: [
    { id: 'gh', kind: 'brew', name: 'gh', have: '2.81.0', latest: '2.82.1' },
    { id: 'my-script', kind: 'info', name: 'my-script', have: '1', latest: '2', note: 'run my-update.sh' },
    { id: 'some-app', kind: 'brew', name: 'some-app', have: '1.2.0', latest: '1.3.0', cask: true },
    ...RESULT.items.filter(i => i.id !== 'gh'),
    { id: 'notes@community', kind: 'info', name: 'notes@community', have: 'dbd4588f9e10', latest: '?', note: 'not compared' },
  ],
}

test('the band keeps the rows of the mods below it, ours on top', async ($, on) => {
  world(on)
  await start($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...band(), surface })
    const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
    const mine = texts.findIndex(t => t.startsWith('↑ 3 tool updates'))
    expect(mine).toBeGreaterThanOrEqual(0)
    expect(texts.indexOf('engine')).toBeGreaterThan(mine)
    expect(await ui.findAll({ type: 'Button' })).toHaveLength(2)
    for (const button of await ui.findAll({ type: 'Button' })) expect(button.props.hotkey).toBeUndefined()
    await ui.unmount()
  }
})

test('a narrow band keeps the count and the buttons, the tail goes first', async ($, on) => {
  world(on)
  await start($)
  const narrow = await $.ui.mount({ ...band({ bodyColumns: 60 }), surface: 'terminal' })
  expect((await narrow.find({ type: 'Text', text: /tool update/ }))?.text).toBe('↑ 3 tool updates')
  expect((await narrow.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['show', 'hide'])
  await narrow.unmount()
  const tiny = await $.ui.mount({ ...band({ bodyColumns: 36 }), surface: 'terminal' })
  expect((await tiny.find({ type: 'Text', text: /↑/ }))?.text).toBe('↑ 3')
  expect((await tiny.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['show'])
  await tiny.unmount()
})

test('the band draws nothing while the pane is shown, and comes back when it closes', async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  await runCommand($)
  await clock.settle()
  expect(w.openArgs[0]).toMatchObject({ id: 'uptick', title: 'Uptick', focus: true, columns: 64 })
  const row = await $.ui.mount({ ...band(), surface: 'terminal' })
  expect((await row.findAll({ type: 'Text' })).map(t => t.text)).toEqual(['engine'])
  w.isShown = false // a tab behind another pane: the row shows
  const behind = await $.ui.mount({ ...band(), surface: 'terminal' })
  expect(await behind.find({ type: 'Text', text: /tool updates/ })).toBeDefined()
  await behind.unmount()
  w.isShown = true
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'close' })
  await ui.unmount()
  expect((await row.find({ type: 'Text', text: /tool updates/ }))?.text).toMatch(/^↑ 3 tool updates/)
  await row.unmount()
})

test('the pane groups its rows: problems, Plugins, Skills, Homebrew, Known issues, Info', async ($, on) => {
  world(on, { cache: FULL })
  await start($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...pane(64), surface })
    const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
    const at = (pattern: RegExp) => texts.findIndex(t => pattern.test(t))
    const order = [/^Uptick · 4 available/, /^\? 2 skills not checked/, /^Plugins \(1\)$/, /^Skills \(1\)$/, /^Homebrew \(2\)$/, /^Known issues \(1\)$/, /^Info \(3\)$/].map(at)
    expect(order.every(i => i >= 0)).toBe(true)
    expect([...order].sort((a, b) => a - b)).toEqual(order)
    expect((await ui.find({ key: 'item:brew:some-app:cask' }))?.text).toMatch(/^some-app +1\.2\.0 → 1\.3\.0 +cask$/)
    expect((await ui.find({ type: 'Text', text: /my-script/ }))?.text).toMatch(/^ {3}my-script +1 → 2 +run my-update\.sh$/)
    expect((await ui.find({ type: 'Text', text: /^ {3}Claude Code/ }))?.text).toMatch(/ {2}updates itself$/)
    expect((await ui.find({ key: 'update-all' }))?.props.label).toBe('Update all (4)')
    await ui.unmount()
  }
})

test('number hotkeys run on across the groups, in display order', async ($, on) => {
  const { clock, w } = world(on, { cache: FULL })
  await start($)
  const ui = await $.ui.mount({ ...pane(64), surface: 'terminal' })
  const items = (await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('item:'))
  expect(items.map(b => [b.key, b.props.hotkey])).toEqual([
    ['item:plugin:lint-helper@community', '1'],
    ['item:skill:code-review', '2'],
    ['item:brew:gh', '3'],
    ['item:brew:some-app:cask', '4'],
  ])
  await ui.press({ key: 'update-all' })
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  expect(w.updates().map(a => a.at(-1))).toEqual(['lint-helper@community', 'code-review', 'gh', 'some-app'])
  await ui.unmount()
})

test('a plugin that could not be compared is a dim Info row, never counted or offered', async ($, on) => {
  world(on, { cache: FULL })
  await start($)
  const ui = await $.ui.mount({ ...pane(64), surface: 'terminal' })
  const row = await ui.find({ type: 'Text', text: /notes@community/ })
  expect(row?.text).toMatch(/^ {3}notes@community +dbd4588f9e10 → \? +not compared$/)
  expect(row?.props.dimColor).toBe(true)
  expect(await ui.find({ key: 'item:info:notes@community' })).toBeUndefined()
  await ui.unmount()
  const strip = await $.ui.mount({ ...band(), surface: 'terminal' })
  expect((await strip.find({ type: 'Text', text: /tool update/ }))?.text).toMatch(/^↑ 4 tool updates · Plugins 1 · Skills 1 · Homebrew 2/)
  await strip.unmount()
})

test('Check now fetches first and shows that it is running', async ($, on) => {
  const { clock, w } = world(on, { slow: 'checker' })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect((await ui.find({ key: 'check' }))?.props).toMatchObject({ hotkey: 'r', label: 'Check now' })
  const pressed = ui.press({ key: 'check' })
  await clock.advance(1000)
  expect(w.ran.at(-1)).toContain('--fetch')
  expect((await ui.find({ type: 'Text', text: /^Uptick/ }))?.text).toBe('Uptick · checking… 0:01')
  expect((await ui.find({ type: 'Text', text: /fetching metadata/ }))?.text).toBe('  plugins, skills, Homebrew · fetching metadata')
  await clock.advance(3000)
  await pressed
  expect((await ui.find({ type: 'Text', text: /^Uptick/ }))?.text).toBe('Uptick · 3 available · checked 09:12')
  await ui.unmount()
})

test('an open that is not placed draws the pane in the band, without digit hotkeys', async ($, on) => {
  const { clock } = world(on, { isPlaced: false })
  await start($)
  expect((await runCommand($)).text).toMatch(/; checking now, the list is above the prompt$/)
  await clock.settle()
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...band(), surface })
    expect((await ui.find({ type: 'Text', text: /^Uptick · / }))?.text).toBe('Uptick · 3 available · checked 09:12')
    const buttons = await ui.findAll({ type: 'Button' })
    expect(buttons.filter(b => /^\d$/.test(String(b.props.hotkey ?? '')))).toEqual([])
    expect(buttons.map(b => b.key)).toContain('item:brew:gh')
    expect(buttons.map(b => b.key)).toContain('band-close')
    expect(buttons.map(b => b.key)).not.toContain('close')
    expect((await ui.findAll({ type: 'Text' })).map(t => t.text)).toContain('engine')
    await ui.unmount()
  }
  const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  await ui.press({ key: 'band-close' })
  expect((await ui.find({ type: 'Text', text: /tool update/ }))?.text).toMatch(/^↑ 3 tool updates/)
  await ui.unmount()
})

test('/uptick is the only command and opens the pane', async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  expect(w.registered).toEqual(['uptick'])
  const reply = await $.command.run({ command: 'uptick', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 140 } })
  await clock.settle()
  expect(reply.text).toMatch(/^Uptick: 3 available/)
  expect(w.opened).toBe(1)
})

test('a run while the pane is not shown is one band row with Show', async ($, on) => {
  const { clock, w } = world(on, { slow: 'gh' })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'item:brew:gh' })
  await clock.advance(1000)
  w.allowUpdates = true
  const pressed = ui.press({ key: 'confirm' })
  await clock.advance(2000)
  w.isShown = false
  const strip = await $.ui.mount({ ...band(), surface: 'terminal' })
  expect((await strip.find({ type: 'Text', text: /Updating/ }))?.text).toBe('… Updating 1 of 1 · gh · 0:02')
  expect((await strip.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['show'])
  await clock.advance(5000)
  await pressed
  await strip.unmount()
  await ui.unmount()
})

test('offline, Check now only checks, and the band says offline and how old the result is', async ($, on) => {
  const offline = { ...RESULT, checkedAt: '2026-10-19T08:30:00+02:00', problems: [{ text: 'could not reach the network, showing the last result', offline: true }] }
  const { clock, w } = world(on, { cache: offline, checker: JSON.stringify(offline) })
  await start($)
  const strip = await $.ui.mount({ ...band(), surface: 'terminal' })
  expect((await strip.find({ type: 'Text', text: /tool update/ }))?.text).toBe('↑ 3 tool updates · Plugins 1 · Skills 1 · Homebrew 1 · offline · checked 10-19')
  await strip.unmount()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'check' })
  await clock.settle()
  expect(w.ran.at(-1)?.[0]).toBe('python3')
  expect(w.ran.at(-1)).not.toContain('--fetch')
  await ui.unmount()
})

test('items are told apart by kind and cask: same-named ones each run their own command', async ($, on) => {
  const twins = {
    ...RESULT,
    items: [
      { id: 'gh', kind: 'skill', name: 'gh', have: 'a1b2c3d', latest: 'e4f5a6b' },
      { id: 'gh', kind: 'brew', name: 'gh', have: '2.81.0', latest: '2.82.1' },
      { id: 'example-tool', kind: 'brew', name: 'example-tool', have: '1.0', latest: '1.1' },
      { id: 'example-tool', kind: 'brew', name: 'example-tool', have: '3', latest: '4', cask: true },
    ],
  }
  const { clock, w } = world(on, { cache: twins, checker: JSON.stringify(twins) })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  const keys = (await ui.findAll({ type: 'Button' })).map(b => b.key).filter(k => k?.startsWith('item:'))
  expect(keys).toEqual(['item:skill:gh', 'item:brew:gh', 'item:brew:example-tool', 'item:brew:example-tool:cask'])
  await ui.press({ key: 'item:brew:example-tool:cask' })
  expect((await ui.find({ type: 'Text', text: /runs/ }))?.text).toBe('  runs   brew upgrade --cask example-tool')
  await ui.press({ key: 'cancel' })
  await ui.press({ key: 'update-all' })
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  expect(w.updates()).toEqual([
    ['npx', '--yes', SKILLS_CLI, 'update', '-g', 'gh'],
    ['brew', 'upgrade', 'gh'],
    ['brew', 'upgrade', 'example-tool'],
    ['brew', 'upgrade', '--cask', 'example-tool'],
  ])
  await ui.unmount()
})

test('updates get no stdin; skills run with telemetry off, brew cannot wait on a sudo prompt', async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'update-all' })
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  const initOf = (last: string) => w.inits[w.ran.findIndex(a => isUpdate(a) && a.at(-1) === last)]
  for (const id of ['lint-helper@community', 'code-review', 'gh']) expect(initOf(id)?.stdin, id).toBe('')
  expect(initOf('code-review')?.env).toEqual({ DISABLE_TELEMETRY: '1', DO_NOT_TRACK: '1' })
  expect(initOf('gh')?.env).toEqual({ SUDO_ASKPASS: '/usr/bin/false' })
  expect(initOf('lint-helper@community')?.env).toBeUndefined()
  await ui.unmount()
})

test('a skill update that matched no installed skill is a failure, not a tick', async ($, on) => {
  const { clock, w } = world(on, { says: { 'code-review': 'No installed skills found matching: code-review' } })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'item:skill:code-review' })
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  expect((await ui.find({ type: 'Text', text: /^Done/ }))?.text).toMatch(/^Done · 0 updated · 1 failed/)
  expect((await ui.find({ type: 'Text', text: /✗ code-review/ }))?.text).toContain('No installed skills found matching')
  expect(await ui.find({ type: 'Text', text: /^Next:/ })).toBeUndefined()
  await ui.unmount()
})

const UV = {
  ...RESULT,
  items: [...RESULT.items, { id: 'example-tool', kind: 'uv', name: 'example-tool', have: '0.9.102', latest: '0.9.110' }],
}

/** Opens the pane on the UV result, picks the uv tool and confirms it. */
async function updateUvTool($: Engine, on: On, options: Parameters<typeof world>[1] = {}) {
  const { clock, w } = world(on, { cache: UV, checker: JSON.stringify(UV), ...options })
  await start($)
  // From the checker, not the cache: the fake cache sits at the POSIX home only.
  await runCommand($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'item:uv:example-tool' })
  const runs = (await ui.find({ type: 'Text', text: /runs/ }))?.text
  const then = await ui.find({ type: 'Text', text: /^ +then / })
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  return { ui, w, runs, then }
}

// uv is opt-in: these tests turn it on as a person would, in kinds.
const WITH_UV = { options: { kinds: 'plugin,skill,brew,uv' } }

test('by default uv tools are not checked or shown: the checker is told the three other kinds', async ($, on) => {
  const { clock, w } = world(on, { cache: UV, checker: JSON.stringify(UV) })
  await start($)
  await runCommand($)
  await clock.settle()
  expect(w.ran[0]?.at(-1)).toBe('--kinds=plugin,skill,brew')
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect(await ui.find({ key: 'item:uv:example-tool' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /uv tools/ })).toBeUndefined()
  expect((await ui.find({ key: 'update-all' }))?.props.label).toBe('Update all (3)')
  await ui.unmount()
})

test('a uv tool is its own group, after Homebrew, numbered on', WITH_UV, async ($, on) => {
  world(on, { cache: UV, checker: JSON.stringify(UV) })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
  expect(texts.indexOf('uv tools (1)')).toBeGreaterThan(texts.indexOf('Homebrew (1)'))
  expect(texts.indexOf('Homebrew (1)')).toBeGreaterThanOrEqual(0)
  expect((await ui.find({ key: 'item:uv:example-tool' }))?.text).toMatch(/^example-tool +0\.9\.102 → 0\.9\.110$/)
  expect((await ui.find({ key: 'item:uv:example-tool' }))?.props.hotkey).toBe('4')
  expect((await ui.find({ key: 'update-all' }))?.props.label).toBe('Update all (4)')
  await ui.unmount()
  const row = await $.ui.mount({ ...band(), surface: 'terminal' })
  expect((await row.find({ type: 'Text', text: /tool updates/ }))?.text).toContain('uv tools 1')
  await row.unmount()
  expect(report(parseResult(JSON.stringify(UV)), parseKinds('plugin,skill,brew,uv'), false, NOW)).toContain(
    '  ↑ example-tool 0.9.102 → 0.9.110\n      uv tool upgrade example-tool',
  )
  // Left out of kinds, it is not offered.
  expect(sortItems(parseResult(JSON.stringify(UV)), parseKinds('plugin,skill,brew')).updatable.map(i => i.kind)).not.toContain('uv')
})

test('a confirmed uv tool runs uv tool upgrade <name>, from home, with no extra env', WITH_UV, async ($, on) => {
  const { ui, w, runs, then } = await updateUvTool($, on)
  expect(runs).toBe('  runs   uv tool upgrade example-tool')
  expect(then).toBeUndefined()
  expect(w.updates()).toEqual([['uv', 'tool', 'upgrade', 'example-tool']])
  const at = w.ran.findIndex(isUpdate)
  expect(w.cwds[at]).toBe(HOME)
  expect(w.inits[at]?.stdin).toBe('')
  expect(w.inits[at]?.env).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /^Done/ }))?.text).toMatch(/^Done · 1 updated/)
  await ui.unmount()
})

test('Windows: uv is a native exe, so its upgrade runs without cmd.exe', WITH_UV, async ($, on) => {
  const { ui, w } = await updateUvTool($, on, { env: { USERPROFILE: WIN_HOME, OS: 'Windows_NT' } })
  expect(w.updates()).toEqual([['uv', 'tool', 'upgrade', 'example-tool']])
  expect(w.cwds[w.ran.findIndex(isUpdate)]).toBe(WIN_HOME)
  await ui.unmount()
})

test('a uv upgrade held by an exact pin is a failure that shows uv hint, not a tick', WITH_UV, async ($, on) => {
  const says = 'Nothing to upgrade\n\nhint: `example-tool` is pinned to `0.9.102` (installed with an exact version pin); reinstall with `uv tool install example-tool@latest` to upgrade to a new version.'
  const { ui } = await updateUvTool($, on, { says: { 'example-tool': says } })
  expect((await ui.find({ type: 'Text', text: /^Done/ }))?.text).toMatch(/^Done · 0 updated · 1 failed/)
  expect((await ui.find({ type: 'Text', text: /✗ example-tool/ }))?.text).toContain('hint: `example-tool` is pinned to `0.9.102`')
  await ui.unmount()
})

// What uv 0.13.7 prints for a tool that is current already (updated between the check and the confirm).
test('a uv upgrade with Nothing to upgrade and no pin hint is a tick', WITH_UV, async ($, on) => {
  const { ui } = await updateUvTool($, on, { says: { 'example-tool': 'Nothing to upgrade' } })
  expect((await ui.find({ type: 'Text', text: /^Done/ }))?.text).toMatch(/^Done · 1 updated/)
  expect(await ui.find({ type: 'Text', text: /✓ example-tool/ })).toBeDefined()
  await ui.unmount()
})

test('while a check runs, the confirm step offers no Update: no two brew processes at once', async ($, on) => {
  const { clock, w } = world(on, { failing: 'gh', slow: 'checker' })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'item:brew:gh' })
  await clock.advance(1000)
  w.allowUpdates = true
  void ui.press({ key: 'confirm' })
  await clock.advance(500) // the run ends; the check after it is under way
  await ui.press({ key: 'retry:brew:gh' })
  expect(await ui.find({ key: 'confirm' })).toBeUndefined()
  expect(await ui.find({ key: 'skip' })).toBeUndefined() // the running check may have read the list already
  expect((await ui.find({ type: 'Text', text: /checking/ }))?.text).toBe('checking… Update appears when the check ends')
  expect(await ui.find({ key: 'cancel' })).toBeDefined()
  await clock.advance(3000)
  expect((await ui.find({ key: 'confirm' }))?.props.label).toBe('Update')
  expect(await ui.find({ key: 'skip' })).toBeDefined()
  await ui.unmount()
})

test('a check that throws still ends: no checking… left behind', async ($, on) => {
  const { clock, w } = world(on, { cache: null })
  await start($)
  w.isEnvGone = true
  await runCommand($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /^Uptick/ }))?.text).toBe('Uptick · check failed')
  expect((await ui.find({ type: 'Text', text: /✗/ }))?.text).toContain('environment gone')
  expect(await ui.find({ key: 'check' })).toBeDefined()
  await ui.unmount()
})

test('a check that cannot start says what to install, by system', async ($, on) => {
  const { clock } = world(on, { cache: null, checker: 'deny:ENOENT: no such file or directory, posix_spawn python3' })
  await start($)
  await runCommand($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /✗/ }))?.text).toMatch(/^ {2}✗ checker could not start \(python3\)/)
  expect((await ui.find({ type: 'Text', text: /install Python/ }))?.text).toBe(
    '    install Python 3 (macOS: xcode-select --install or brew install python; Linux: the python3 package)',
  )
  await ui.unmount()
})

test('Windows: a check that cannot start points to python.org and the py launcher', async ($, on) => {
  const { clock } = world(on, { cache: null, checker: 'deny:spawn py ENOENT', env: { USERPROFILE: WIN_HOME, OS: 'Windows_NT' } })
  await start($)
  await runCommand($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /install Python/ }))?.text).toBe(
    '    install Python 3: winget install -e --id Python.Python.3.13, or python.org with its py launcher',
  )
  await ui.unmount()
})

test('macOS without the developer tools: the python3 stub fails, the hint says what to install', async ($, on) => {
  const { clock } = world(on, { cache: null, checker: 'xcrun: error: invalid active developer path' })
  await start($)
  await runCommand($)
  await clock.settle()
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /install Python 3 \(macOS/ })).toBeDefined()
  await ui.unmount()
})

test('the kinds option reaches the checker', { options: { kinds: 'plugin' } }, async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  await runCommand($)
  await clock.settle()
  expect(w.ran[0]).toContain('--kinds=plugin')
})

test('the Done view keeps full names after the check that follows the run', async ($, on) => {
  const onlySelf = { ...RESULT, items: [RESULT.items[3]], problems: [] }
  const { clock, w } = world(on, { checker: JSON.stringify(onlySelf) })
  await start($)
  const ui = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  await ui.press({ key: 'update-all' })
  await clock.advance(1000)
  w.allowUpdates = true
  await ui.press({ key: 'confirm' })
  await clock.settle()
  expect((await ui.find({ type: 'Text', text: /^Done/ }))?.text).toMatch(/^Done · 3 updated/)
  expect((await ui.find({ type: 'Text', text: /lint-helper/ }))?.text).toMatch(/✓ lint-helper@community {2}1\.4\.0 → 1\.5\.0$/)
  await ui.unmount()
})

// The opt-in scan marks the row an update fixes: a count and a level, never an id or a package name.
const VULNS = {
  ...RESULT,
  items: [
    { ...RESULT.items[0], vulns: { count: 3, level: 'high' } },
    RESULT.items[1],
    { ...RESULT.items[2], vulns: { count: 1, level: 'unknown' } },
  ],
  problems: [],
}

test('a row with known vulnerabilities says how many and how bad, in one and two lines and in the report', async ($, on) => {
  world(on, { cache: VULNS, checker: JSON.stringify(VULNS) })
  await start($)
  const wide = await $.ui.mount({ ...pane(68), surface: 'terminal' })
  expect((await wide.find({ key: 'item:plugin:lint-helper@community' }))?.text).toMatch(/^lint-helper@community +1\.4\.0 → 1\.5\.0 +3 vulns \(high\)$/)
  expect((await wide.find({ key: 'item:brew:gh' }))?.text).toMatch(/^gh +2\.81\.0 → 2\.82\.1 +1 vuln$/)
  expect((await wide.find({ key: 'item:skill:code-review' }))?.text).not.toContain('vuln')
  await wide.unmount()
  const narrow = await $.ui.mount({ ...pane(50), surface: 'terminal' })
  expect((await narrow.find({ type: 'Text', text: /1\.4\.0 → 1\.5\.0/ }))?.text).toBe('     1.4.0 → 1.5.0  3 vulns (high)')
  await narrow.unmount()
  expect(report(parseResult(JSON.stringify(VULNS)), parseKinds('plugin,skill,brew'), false, NOW)).toContain(
    '  ↑ lint-helper@community 1.4.0 → 1.5.0  ! 3 vulns (high)\n      claude plugin update lint-helper@community',
  )
})

test('parseResult keeps vulns only in the checker shape', () => {
  const item = (vulns: unknown) => parseResult(JSON.stringify({ items: [{ id: 'gh', kind: 'brew', vulns }] }))?.items[0]?.vulns
  expect(item({ count: 2, level: 'critical' })).toEqual({ count: 2, level: 'critical' })
  for (const bad of [{ count: 0, level: 'high' }, { count: 1.5, level: 'high' }, { count: '2', level: 'high' }, { count: 2, level: '\u001b[31m' }, 'x', null])
    expect(item(bad)).toBeUndefined()
})

test('vulns in kinds reaches the checker and adds no group', { options: { kinds: 'plugin,skill,brew,vulns' } }, async ($, on) => {
  const { clock, w } = world(on)
  await start($)
  await runCommand($)
  await clock.settle()
  expect(w.ran[0]?.at(-1)).toBe('--kinds=plugin,skill,brew,vulns')
  expect(parseKinds('plugin,skill,brew,vulns')).toEqual(['plugin', 'skill', 'brew'])
})

// The session start: the list is ready without /uptick, which the desktop app cannot run, and nothing reaches the conversation.
test('a first session checks in the background at start, with a fetch; the band shows it, the context gets nothing', async ($, on) => {
  const { clock, w } = world(on, { cache: null })
  expect(await start($)).toEqual({ cwd: '/' })
  await clock.settle()
  expect(w.ran).toHaveLength(1)
  expect(w.ran[0]).toContain('--fetch')
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...band(), surface })
    expect((await ui.find({ type: 'Text', text: /tool update/ }))?.text).toMatch(/^↑ 3 tool updates/)
    await ui.unmount()
  }
  // Another start within minutes, a reload or a session started alongside, checks no second time.
  await start($)
  await clock.settle()
  expect(w.ran).toHaveLength(1)
})

test('at start: a result over a day old is checked again, and claude -p never checks at start', async ($, on) => {
  const { clock, w } = world(on, { cache: { ...RESULT, checkedAt: '2026-10-19T09:12:00+02:00' } })
  await start($, false)
  await clock.settle()
  expect(w.ran).toHaveLength(0)
  await start($)
  await clock.settle()
  expect(w.ran).toHaveLength(1)
})

test('a session start fetches once a day, also after checks without a fetch; an offline fetch is tried again', async ($, on) => {
  const offline = { ...RESULT, problems: [{ text: 'could not reach the npm registry', offline: true }] }
  const { clock, w } = world(on, { cache: { ...RESULT, fetched: false }, checker: JSON.stringify(offline) })
  await start($)
  await clock.settle()
  expect(w.ran.map(argv => argv.includes('--fetch'))).toEqual([true])
  await clock.advance(11 * 60_000)
  await start($)
  await clock.settle()
  expect(w.ran.map(argv => argv.includes('--fetch'))).toEqual([true, true])
})

test('a fetch that reached the network holds the next one off for a day', async ($, on) => {
  const { clock, w } = world(on, { cache: { ...RESULT, fetched: false } })
  await start($)
  await clock.settle()
  expect(w.ran).toHaveLength(1)
  await clock.advance(11 * 60_000)
  await start($)
  await clock.settle()
  expect(w.ran).toHaveLength(1)
})

test('a first check that cannot start shows in the band with what to install; Show opens the pane', async ($, on) => {
  const { clock, w } = world(on, { cache: null, checker: 'deny:spawn py ENOENT', env: { USERPROFILE: WIN_HOME, OS: 'Windows_NT' } })
  await start($)
  await clock.settle()
  const ui = await $.ui.mount({ ...band(), surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /check failed/ }))?.text).toBe(
    '✗ Uptick check failed · install Python 3: winget install -e --id Python.Python.3.13, or python.org with its py launcher',
  )
  await ui.press({ key: 'show' })
  expect(w.opened).toBe(1)
  await clock.settle() // the open checks again
  await ui.unmount()
})

test('/uptick answers in one line: the list is in the pane, not in the conversation', async ($, on) => {
  const { clock } = world(on)
  await start($)
  expect((await runCommand($)).text).toBe('Uptick: 3 available (checked 09:12); checking now, the list is in the pane')
  await clock.settle()
})

test('a time ahead of the clock, which was set back since, does not hold the session-start check off', async ($, on) => {
  const { clock, w } = world(on, { cache: { ...RESULT, checkedAt: '2026-10-23T09:12:00+02:00' } })
  await start($)
  await clock.settle()
  expect(w.ran).toHaveLength(1)
})

test('a refused environment read at start costs nothing else: the band comes back once it reads again', async ($, on) => {
  const { clock, w } = world(on)
  w.isEnvGone = true
  await start($)
  expect(w.registered).toEqual(['uptick'])
  w.isEnvGone = false
  await clock.advance(900_000)
  const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /tool update/ }))?.text).toMatch(/^↑ 3 tool updates/)
  await ui.unmount()
})
