import { atom, read, update } from 'claude-code'
import type { Args, EngineInterface, Next, PluginOptions, Register, RenderChildren, RenderInput, Timer } from 'claude-code'

import type { CheckError, CheckResult, Run, Step, ToolItem, View } from '../types'
import {
  AFTER,
  argvFor,
  checkerArgv,
  checkHint,
  clean,
  cleanLines,
  commandLine,
  elapsed,
  fit,
  hasOffline,
  keyOf,
  knownBrokenPath,
  lastLine,
  NO_PYTHON,
  onHost,
  parseKinds,
  parseResult,
  report,
  signature,
  sortItems,
  UPDATE_ENV,
  vulnsText,
  when,
  withSkip,
} from './logic.ts'

const PANE = 'uptick'
const TITLE = 'Uptick'
const COMMAND = 'uptick'
const TEN_MINUTES = 600_000
const FIFTEEN_MINUTES = 900_000
const SCOPE: Record<string, string> = { plugin: 'plugins', skill: 'skills', brew: 'Homebrew', uv: 'uv tools', winget: 'winget packages' }
const GROUP: Record<string, string> = { plugin: 'Plugins', skill: 'Skills', brew: 'Homebrew', uv: 'uv tools', winget: 'winget' }
const DAY = 86_400_000

const resultA = atom({ plugin: 'uptick', key: 'result' } as const, null)
const checkA = atom({ plugin: 'uptick', key: 'check' } as const, null)
const checkErrorA = atom({ plugin: 'uptick', key: 'checkError' } as const, null)
const viewA = atom({ plugin: 'uptick', key: 'view' } as const, { name: 'list' })
const runA = atom({ plugin: 'uptick', key: 'run' } as const, null)
const nowA = atom({ plugin: 'uptick', key: 'now' } as const, 0)
const hiddenA = atom({ plugin: 'uptick', key: 'hiddenFor' } as const, null)
const whereA = atom({ plugin: 'uptick', key: 'where' } as const, null)

type Engine = EngineInterface
type DrawInput = RenderInput<'Pane'> | RenderInput<'AbovePrompt'>

const isRunning = (run: Run | null) => run !== null && run.endedAt === undefined
const message = (err: unknown) => (err instanceof Error ? err.message : String(err))

// One load's configuration and session facts. A reload starts a fresh module, and runs register and
// session.start again; until session.start says otherwise, the read-only (headless) path applies.
const config: { options: PluginOptions; kinds: string[]; isInteractive: boolean; isWindows: boolean } = {
  options: {},
  kinds: [],
  isInteractive: false,
  isWindows: false,
}
let ticker: Timer | undefined
let cacheTimer: Timer | undefined

// The engine names no platform; Windows sets OS, and has USERPROFILE where it has no HOME.
async function isWindows($: Engine): Promise<boolean> {
  const os = await $.env.get('OS')
  return os === 'Windows_NT'
}

async function homeDir($: Engine): Promise<string | undefined> {
  const home = await $.env.get('HOME')
  if (home) return home
  const profile = await $.env.get('USERPROFILE')
  return profile || undefined
}

// Commands run from the home folder, not the session's: there a project's own tool shims or npm config
// could stand in for the `npx skills` or `npm view` the person approved.
async function homeCwd($: Engine): Promise<{ cwd?: string }> {
  const home = await homeDir($)
  return home ? { cwd: home } : {}
}

async function cachePath($: Engine): Promise<string | undefined> {
  // The checker's own default: $XDG_CACHE_HOME or ~/.cache, then uptick/last.json.
  const xdg = await $.env.get('XDG_CACHE_HOME')
  const home = await homeDir($)
  return xdg ? `${xdg}/uptick/last.json` : home ? `${home}/.cache/uptick/last.json` : undefined
}

async function readCache($: Engine): Promise<CheckResult | null> {
  const path = await cachePath($)
  if (!path) return null
  try {
    const text = await $.fs.read(path)
    return parseResult(text)
  } catch {
    return null // no cache yet
  }
}

// A scheduled check (extras/) rewrites the cache while sessions run: a newer result replaces the one shown.
async function refreshFromCache($: Engine) {
  const check = await read($, checkA)
  if (check) return
  try {
    const cached = await readCache($)
    const current = await read($, resultA)
    if (cached && (!current || Date.parse(cached.checkedAt) > Date.parse(current.checkedAt))) await publish($, cached)
  } catch (err) {
    $.ui.log(`uptick: cache not read: ${message(err)}`, { to: 'debug' })
  }
}

async function publish($: Engine, result: CheckResult) {
  await update($, resultA, () => result)
  // A fetch that reached the network, ours or a scheduled one: the next session start fetches a day later.
  if (result.fetched && !hasOffline(result.problems)) await $.store.set('fetchedAt', Date.parse(result.checkedAt) || 0)
  if (config.options.hint === 'status') {
    const n = sortItems(result, config.kinds).updatable.length
    $.ui.status(n ? `↑ ${n} tool update${n === 1 ? '' : 's'} · /uptick` : undefined)
  }
}

// The elapsed timers redraw from `now`; one ticker while a check or a run is under way.
async function tick($: Engine) {
  const now = await $.clock.now()
  await update($, nowA, () => now)
}

function startTicker($: Engine) {
  if (ticker) return
  ticker = $.clock.every(1000, () => void tick($))
}

async function stopTickerWhenIdle($: Engine) {
  const check = await read($, checkA)
  const run = await read($, runA)
  if (!check && !isRunning(run)) {
    ticker?.cancel()
    ticker = undefined
  }
}

async function runChecker($: Engine, isFetch: boolean): Promise<{ result: CheckResult } | { error: CheckError }> {
  const root = $.plugin.root
  const onWindows = await isWindows($)
  const cache = await cachePath($)
  const argv = checkerArgv(root, cache, isFetch, config.options, onWindows)
  const hint = (failure: Parameters<typeof checkHint>[0]) => checkHint(failure, onWindows, root)
  try {
    const cwd = await homeCwd($)
    const ran = await $.process.run(argv, { timeoutMs: TEN_MINUTES, ...cwd })
    const result = parseResult(ran.stdout)
    if (result) return { result }
    const text = `checker exited ${ran.exitCode}: ${lastLine(ran.stderr) || 'no JSON output'}`
    return { error: { text, hint: hint(NO_PYTHON.test(`${ran.stderr}\n${ran.stdout}`) ? 'start' : 'output') } }
  } catch (err) {
    const text = message(err)
    return /time/i.test(text)
      ? { error: { text: 'check timed out', hint: hint('timeout') } }
      : { error: { text: `checker could not start (${argv[0]}): ${clean(text)}`, hint: hint('start') } }
  }
}

async function startCheck($: Engine, isFetch: boolean) {
  const startedAt = await $.clock.now()
  let isStarted = false
  await update($, checkA, check => {
    isStarted = check === null
    return check ?? { startedAt, isFetch }
  })
  if (!isStarted) return
  try {
    await update($, checkErrorA, () => null)
    startTicker($)
    const out = await runChecker($, isFetch)
    if ('result' in out) await publish($, out.result)
    await update($, checkErrorA, () => ('error' in out ? out.error : null))
  } catch (err) {
    await update($, checkErrorA, () => ({ text: clean(message(err)), hint: '' }))
  } finally {
    await update($, checkA, () => null)
    await stopTickerWhenIdle($)
  }
}

// The list is ready without /uptick, which the desktop app cannot run: a session start checks, with a fetch, when
// there is no result or no fetch for a day (a result over a day old means that too: every check rewrites it). Only
// the pane and the band show it, never the conversation.
async function checkIfDue($: Engine, cached: CheckResult | null) {
  const now = await $.clock.now()
  // A time ahead of the clock (set back since) counts as due, or the check would wait for the clock to catch up.
  const recent = (at: unknown, ms: number) => typeof at === 'number' && at <= now && now - at < ms
  const fetchedAt = await $.store.get('fetchedAt')
  const isFetch = !recent(fetchedAt, DAY)
  if (cached && !isFetch) return
  // Sessions started together, or a reload, check once.
  const triedAt = await $.store.get('autoCheckAt')
  if (recent(triedAt, TEN_MINUTES)) return
  await $.store.set('autoCheckAt', now)
  void startCheck($, isFetch)
}

async function openPane($: Engine) {
  const run = await read($, runA)
  if (!isRunning(run)) {
    await update($, runA, () => null)
    await update($, viewA, () => ({ name: 'list' }))
  }
  const result = await read($, resultA)
  // 12 rows besides the items: the heading, the ✓ line, six group headers, the action row and its margin,
  // and two for a Known issue's detail line or a wrapped problem.
  const rows = Math.min(24, 12 + (result ? result.items.length + result.problems.length + result.skipped.length : 2))
  const opened = await $.ui.open({ id: PANE, title: TITLE, focus: true, rows, columns: 64 })
  // Not placed (no surface that places panes): the pane's tree draws in the band instead.
  await update($, whereA, () => (opened.isPlaced ? 'pane' : 'band'))
  if (!isRunning(run)) void startCheck($, config.options.fetchOnOpen === true)
}

async function patchStep($: Engine, index: number, patch: Partial<Step>) {
  await update($, runA, run => run && { ...run, steps: run.steps.map((s, i) => (i === index ? { ...s, ...patch } : s)) })
}

/** Runs the confirmed items one by one; reached only from the confirm state's `[ Update ]` Button. */
async function startRun($: Engine, keys: readonly string[]) {
  const check = await read($, checkA)
  if (check) return // the check may be running brew itself
  const result = await read($, resultA)
  const { updatable } = sortItems(result, config.kinds)
  const steps: Step[] = []
  for (const key of keys) {
    const item = updatable.find(i => keyOf(i) === key)
    const argv = item && argvFor(item)
    if (item && argv) steps.push({ key, name: item.name, kind: item.kind, have: item.have, latest: item.latest, argv, status: 'queued' })
  }
  if (!steps.length) return
  const startedAt = await $.clock.now()
  let isStarted = false
  await update($, runA, run => {
    isStarted = !isRunning(run)
    return isStarted ? { steps, startedAt, isStopping: false } : run
  })
  if (!isStarted) return
  await update($, viewA, () => ({ name: 'result' }))
  startTicker($)
  // One $.process.run per item, so no live output. Move to $.process.spawn once a stream started from a
  // button press is known to outlive the press.
  for (const [index, step] of steps.entries()) {
    const current = await read($, runA)
    if (current?.isStopping) {
      await update($, runA, run => run && { ...run, steps: run.steps.map(s => (s.status === 'queued' ? { ...s, status: 'skipped' as const } : s)) })
      break
    }
    await patchStep($, index, { status: 'running' })
    try {
      const env = UPDATE_ENV[step.kind]
      const onWindows = await isWindows($)
      const cwd = await homeCwd($)
      const ran = await $.process.run(onHost(step.argv, onWindows), { timeoutMs: TEN_MINUTES, stdin: '', ...(env ? { env } : {}), ...cwd })
      const log = cleanLines(`${ran.stdout}\n${ran.stderr}`.trim(), 20_000)
      // The skills CLI exits 0 when no installed skill has the name, and uv when an exact pin holds the tool
      // (uv's bare "Nothing to upgrade" means current already): nothing was updated.
      const missed =
        step.kind === 'skill' ? /No installed skills found matching.*/.exec(log)?.[0] : step.kind === 'uv' ? /hint: .* is pinned .*/.exec(log)?.[0] : undefined
      await patchStep(
        $,
        index,
        ran.exitCode === 0 && !missed
          ? { status: 'ok', log }
          : missed
            ? { status: 'failed', errorLine: clean(missed), log }
            : { status: 'failed', exitCode: ran.exitCode, errorLine: lastLine(ran.stderr) || lastLine(ran.stdout) || `exit ${ran.exitCode}`, log },
      )
    } catch (err) {
      const text = message(err)
      await patchStep($, index, { status: 'failed', errorLine: /time/i.test(text) ? 'timed out after 10 minutes' : `could not start ${step.argv[0]}`, log: clean(text, 2_000) })
    }
  }
  const endedAt = await $.clock.now()
  const ended = await update($, runA, run => run && { ...run, endedAt })
  await stopTickerWhenIdle($)
  // In the band fallback the result is on screen already.
  const where = await read($, whereA)
  let isShown = where === 'band'
  if (!isShown) {
    const panes = await $.ui.panes()
    isShown = panes.some(p => p.id === PANE && p.isShown)
  }
  if (!isShown && ended) {
    const ok = ended.steps.filter(s => s.status === 'ok').length
    const failed = ended.steps.filter(s => s.status === 'failed').length
    $.ui.toast(`${TITLE}: ${ok} updated${failed ? `, ${failed} failed` : ''}`)
  }
  void startCheck($, false) // so the band and the list count what is still outdated
}

function refuseSkip($: Engine, item: ToolItem, why: string) {
  $.ui.toast(`${TITLE}: ${item.name} not skipped: ${why}`)
}

// Skip goes through the checker's known-broken list, so the pane, the band and `-p` all hold the version back
// until upstream moves past it. Only a missing file counts as an empty list: one it cannot read stays untouched.
async function skipVersion($: Engine, item: ToolItem) {
  const xdg = await $.env.get('XDG_CONFIG_HOME')
  // The checker's `~`: Python reads USERPROFILE on Windows, not HOME. Its working folder is homeCwd's, homeDir.
  const onWindows = await isWindows($)
  const profile = await $.env.get('USERPROFILE')
  const cwd = await homeDir($)
  const path = knownBrokenPath(config.options, xdg, onWindows ? profile || undefined : cwd, cwd)
  if (!path) {
    refuseSkip($, item, 'cannot tell where the known-broken list is; set knownBroken to a full path')
    return
  }
  let text: string | undefined
  try {
    const found = await $.fs.read(path)
    text = found
  } catch (err) {
    if (!/ENOENT/.test(message(err))) {
      refuseSkip($, item, `${path}: could not be read (${clean(message(err))})`)
      return
    }
  }
  const now = await $.clock.now()
  const skipped = withSkip(text, item, new Date(now).toISOString().slice(0, 10))
  if ('refused' in skipped) {
    refuseSkip($, item, `${path}: ${skipped.refused}`)
    return
  }
  try {
    await $.fs.write(path, skipped.text)
  } catch (err) {
    refuseSkip($, item, `${path}: could not be written (${clean(message(err))})`)
    return
  }
  $.ui.toast(`${TITLE}: ${item.name} ${item.latest} skipped until a newer version`)
  await update($, viewA, () => ({ name: 'list' }))
  void startCheck($, false)
}

// Only types the command into the prompt: the person runs it with Enter.
async function reloadPlugins($: Engine) {
  await $.prompt.fill({ text: '/reload-plugins' })
}

async function closePane($: Engine) {
  const run = await read($, runA)
  if (!isRunning(run)) {
    await update($, runA, () => null)
    await update($, viewA, () => ({ name: 'list' }))
  }
  await $.ui.close({ id: PANE })
  await update($, whereA, () => null)
}

/**
 * The pane's tree, for its own pane or, when an open was not placed, for the band. In the band no
 * Button has a digit hotkey (a digit typed into an empty prompt can press one), and Close sits on the
 * heading row with no hotkey.
 */
async function draw($: Engine, e: DrawInput, isBand: boolean) {
  const { Box, Text, Button, Code } = $.ui.resolve(e)
  const { kinds } = config
  const result = await read($, resultA)
  const check = await read($, checkA)
  const checkError = await read($, checkErrorA)
  const view = await read($, viewA)
  const run = await read($, runA)
  await read($, nowA) // subscribes the drawing to the ticker
  const now = await $.clock.now()
  const w = e.props.bodyColumns
  const sorted = sortItems(result, kinds)

  // Columns: name capped at 40 % of the width, then versions, then the tag or note. A run's steps count
  // too: after the check that follows a run, the updated items are no longer in the list.
  const rowsOf = [...sorted.updatable, ...sorted.broken, ...sorted.info, ...(run?.steps ?? [])]
  const nameW = Math.max(8, Math.min(Math.max(0, ...rowsOf.map(i => i.name.length)), Math.floor(w * 0.4)))
  const verW = Math.max(0, Math.min(Math.max(0, ...rowsOf.map(i => i.have.length + i.latest.length + 3)), w - 3 - nameW - 4 - 8))

  const setView = async (v: View) => {
    await update($, viewA, () => v)
  }
  const confirmFor = (ids: string[]) => setView({ name: 'confirm', ids })
  const digit = (index: number) => (!isBand && index < 9 ? { hotkey: String(index + 1) } : {})

  // In the band the heading row ends with Close; the pane's frame has its own close mark and `q`.
  const headRow = (text: RenderChildren) =>
    isBand ? (
      <Box flexDirection="row" gap={2} width={w}>
        {text}
        <Box flexGrow={1} />
        <Button key="band-close" plain label="Close" onPress={() => closePane($)} />
      </Box>
    ) : (
      text
    )
  const heading = (status: string) =>
    headRow(
      <Text wrap="truncate-end">
        <Text weight="bold">{TITLE}</Text>
        <Text dimColor> · {status}</Text>
      </Text>,
    )
  const actions = (...buttons: RenderChildren[]) => (
    <Box marginTop={1} gap={3} flexWrap="wrap">
      {buttons.filter(Boolean)}
    </Box>
  )
  const close = isBand ? null : <Button key="close" plain hotkey="q" role="dismiss" label="Close" onPress={() => closePane($)} />
  // Check now fetches first (marketplaces, `brew update`); it can take a minute.
  // Offline it only checks: a fetch would wait out every timeout for nothing.
  const checkNow = (
    <Button
      key="check"
      plain
      hotkey="r"
      label="Check now"
      onPress={async () => {
        await update($, runA, run => (isRunning(run) ? run : null))
        await update($, viewA, () => ({ name: 'list' }))
        await startCheck($, !hasOffline(sorted.problems))
      }}
    />
  )

  const itemRow = (item: ToolItem, index: number) => {
    const key = `item:${keyOf(item)}`
    const hotkey = digit(index)
    const lead = 'hotkey' in hotkey ? '' : '   '
    const select = () => confirmFor([keyOf(item)])
    const cask = item.cask === true ? 'cask' : ''
    const flag = vulnsText(item)
    if (w < 40)
      return (
        <Button key={key} plain {...hotkey} onPress={select}>
          {lead}
          {fit(item.name, w - 6).trimEnd()} <Text color="warning">↑</Text>
        </Button>
      )
    const versions = [<Text dimColor>{item.have}</Text>, ' → ', <Text color="warning">{item.latest}</Text>]
    if (w < 60)
      return (
        <Box key={key} flexDirection="column">
          <Button key={key} plain {...hotkey} onPress={select}>
            {lead}
            {fit(item.name, w - 3).trimEnd()}
          </Button>
          <Text wrap="truncate-end">
            {'     '}
            {versions}
            {cask ? <Text dimColor>{`  ${cask}`}</Text> : null}
            {flag ? <Text color="error">{`  ${flag}`}</Text> : null}
          </Text>
        </Box>
      )
    const pad = ' '.repeat(Math.max(0, verW - item.have.length - item.latest.length - 3))
    return (
      <Button key={key} plain {...hotkey} onPress={select}>
        {lead}
        {fit(item.name, nameW)}
        {'  '}
        {versions}
        {cask || flag ? pad : null}
        {cask ? ['  ', <Text dimColor>{cask}</Text>] : null}
        {flag ? ['  ', <Text color="error">{flag}</Text>] : null}
      </Button>
    )
  }

  const infoRow = (item: ToolItem) => {
    const note = item.note ?? 'updates itself'
    if (w < 40) return <Text dimColor wrap="truncate-end">{`   ${item.name}`}</Text>
    if (w < 60)
      return (
        <Box flexDirection="column">
          <Text dimColor wrap="truncate-end">{`   ${item.name}`}</Text>
          <Text dimColor wrap="truncate-end">{`     ${item.have} → ${item.latest}  ${note}`}</Text>
        </Box>
      )
    return <Text dimColor wrap="truncate-end">{`   ${fit(item.name, nameW)}  ${fit(`${item.have} → ${item.latest}`, verW)}  ${note}`}</Text>
  }

  // The checker's note repeats the group title, so the pane says `held back`; the report keeps the note.
  const brokenRow = (item: ToolItem) => (
    <Box flexDirection="column">
      <Text wrap="truncate-end">
        {'   '}
        <Text color="warning">!</Text> {w < 60 ? item.name : fit(item.name, Math.max(1, nameW - 2))}
        {w < 60 ? null : <Text dimColor>{`  ${fit(`${item.have} → ${item.latest}`, verW)}  `}</Text>}
        {w < 60 ? null : (
          <Text dimColor italic>
            held back
          </Text>
        )}
      </Text>
      {item.detail ? (
        <Box paddingLeft={5}>
          <Text dimColor italic wrap={w >= 60 ? 'wrap' : 'truncate-end'}>
            {item.detail}
          </Text>
        </Box>
      ) : null}
    </Box>
  )

  const problemRows = () =>
    sorted.problems.map(p => (
      <Box paddingLeft={3}>
        <Text wrap="wrap">
          <Text color="error">?</Text> {p.text}
          {p.hint ? ` (${p.hint})` : ''}
        </Text>
      </Box>
    ))

  const groupHead = (title: string, n: number) => <Text dimColor>{`${title} (${n})`}</Text>

  // Plugins, Skills, Homebrew, uv tools, winget, Known issues, Info; the hotkeys run on across groups.
  const groups = () => [
    ...kinds.flatMap(kind => {
      const group = sorted.updatable.filter(i => i.kind === kind)
      return group.length ? [groupHead(GROUP[kind] ?? kind, group.length), ...group.map(i => itemRow(i, sorted.updatable.indexOf(i)))] : []
    }),
    ...(sorted.broken.length ? [groupHead('Known issues', sorted.broken.length), ...sorted.broken.map(brokenRow)] : []),
    ...(sorted.info.length ? [groupHead('Info', sorted.info.length), ...sorted.info.map(infoRow)] : []),
    ...(result?.skipped ?? []).map(s => <Text dimColor wrap="truncate-end">{`   ${s}`}</Text>),
  ]

  const plainRow = (item: ToolItem) => (
    <Text dimColor wrap="truncate-end">
      {w < 60 ? `  ${item.name}` : `  ${fit(item.name, nameW)}  ${item.have} → ${item.latest}`}
    </Text>
  )

  const stepRow = (step: Step, isDone: boolean) => {
    const name = w < 40 ? step.name : fit(step.name, nameW)
    const glyph =
      step.status === 'ok' ? <Text color="success">✓</Text> : step.status === 'failed' ? <Text color="error">✗</Text> : <Text dimColor>{step.status === 'running' ? '…' : '·'}</Text>
    const word =
      step.status === 'ok'
        ? isDone
          ? `${step.have} → ${step.latest}`
          : 'updated'
        : step.status === 'failed'
          ? step.exitCode === undefined
            ? (step.errorLine ?? 'failed')
            : `exit ${step.exitCode}`
          : step.status === 'skipped'
            ? 'skipped (stopped)'
            : step.status
    return (
      <Box flexDirection="column">
        <Text wrap="truncate-end">
          {'  '}
          {glyph} {name}
          {w < 40 ? '' : '  '}
          {w < 40 ? '' : step.status === 'ok' || step.status === 'failed' ? word : <Text dimColor>{word}</Text>}
        </Text>
        {isDone && step.status === 'failed' && step.exitCode !== undefined && step.errorLine ? (
          <Text wrap="wrap">{`      ${step.errorLine}`}</Text>
        ) : null}
      </Box>
    )
  }

  // Running: a progress list; no close, no reload (a reload would kill the child).
  if (run && isRunning(run)) {
    const current = run.steps.findIndex(s => s.status === 'running')
    const step = run.steps[current]
    return (
      <Box flexDirection="column">
        <Text wrap="truncate-end">
          <Text weight="bold">
            Updating {current + 1} of {run.steps.length}
          </Text>
          <Text dimColor>
            {step ? ` · ${step.name}` : ''} · {elapsed(now - run.startedAt)}
          </Text>
        </Text>
        {run.steps.map(s => stepRow(s, false))}
        {actions(
          run.isStopping ? (
            <Text dimColor>stops after this item</Text>
          ) : (
            <Button key="stop" plain hotkey="s" label="Stop after this item" onPress={async () => { await update($, runA, r => r && { ...r, isStopping: true }) }} />
          ),
        )}
      </Box>
    )
  }

  if (run && view.name === 'output') {
    const log = run.steps
      .filter(s => s.status === 'failed')
      .map(s => `$ ${s.argv.join(' ')}\n${s.log ?? ''}`)
      .join('\n\n')
    return (
      <Box flexDirection="column">
        {heading('output')}
        <Code source={log || 'no output'} wrap="truncate-end" />
        {actions(<Button key="back" plain hotkey="b" label="Back" onPress={() => setView({ name: 'result' })} />)}
      </Box>
    )
  }

  if (run && view.name === 'result') {
    const count = (status: Step['status']) => run.steps.filter(s => s.status === status).length
    const [ok, failed, skipped] = [count('ok'), count('failed'), count('skipped')]
    const parts = [`${ok} updated`, ...(failed ? [`${failed} failed`] : []), ...(skipped ? [`${skipped} skipped`] : []), elapsed((run.endedAt ?? now) - run.startedAt)]
    const okKinds = new Set(run.steps.filter(s => s.status === 'ok').map(s => s.kind))
    const failedSteps = run.steps.filter(s => s.status === 'failed')
    return (
      <Box flexDirection="column">
        {headRow(
          <Text wrap="truncate-end">
            <Text weight="bold">Done</Text>
            <Text dimColor> · {parts.join(' · ')}</Text>
          </Text>,
        )}
        {run.steps.map(s => stepRow(s, true))}
        {okKinds.has('plugin') || okKinds.has('skill') ? (
          <Box marginTop={1} flexDirection="column">
            {okKinds.has('plugin') ? <Text wrap="wrap">Next: {AFTER.plugin.next}</Text> : null}
            {okKinds.has('skill') ? <Text wrap="wrap">Next: {AFTER.skill}.</Text> : null}
          </Box>
        ) : null}
        {actions(
          ...failedSteps.slice(0, 9).map((s, i) => <Button key={`retry:${s.key}`} plain {...digit(i)} label={`Retry ${s.name}`} onPress={() => confirmFor([s.key])} />),
          failedSteps.length ? <Button key="output" plain hotkey="o" label="Show output" onPress={() => setView({ name: 'output' })} /> : null,
          okKinds.has('plugin') ? <Button key="reload" plain hotkey="l" label="Reload plugins" onPress={() => reloadPlugins($)} /> : null,
          check ? null : checkNow, // the re-check after the run is under way: a press would be dropped
          close,
        )}
      </Box>
    )
  }

  if (view.name === 'confirm') {
    const items = view.ids.map(key => sorted.updatable.find(i => keyOf(i) === key)).filter((i): i is ToolItem => i !== undefined)
    const cancel = <Button key="cancel" plain hotkey="n" label="Cancel" autoFocus onPress={() => setView({ name: 'list' })} />
    // No hotkey: a letter typed into the focused pane must never run an update. Update runs only when its
    // Button is activated: Tab or the arrows onto it, then Enter, or a click. Cancel holds the focus.
    // While a check runs there is no Update: the check may be running brew.
    const confirm = (label: string) =>
      check ? (
        <Text key="wait" dimColor>
          checking… Update appears when the check ends
        </Text>
      ) : (
        <Button
          key="confirm"
          variant="primary"
          label={label}
          onPress={async () => {
            const current = await read($, viewA)
            if (current.name === 'confirm') void startRun($, current.ids)
          }}
        />
      )
    const howTo = <Text dimColor wrap="truncate-end">Tab or arrows to Update, then Enter</Text>
    const [first] = items
    if (items.length === 1 && first) {
      const after = first.kind === 'plugin' ? AFTER.plugin.one : first.kind === 'skill' ? AFTER.skill : ''
      // No hotkey either: a typed letter must not change the known-broken list. A check under way may have read
      // the list already, so Skip waits for it like Update.
      const skip = check ? null : <Button key="skip" plain label={`Skip ${first.latest}`} onPress={() => skipVersion($, first)} />
      return (
        <Box flexDirection="column">
          <Text weight="bold" wrap="wrap">
            Update {first.name} {first.have} → {first.latest}?
          </Text>
          <Text wrap="wrap">{`  runs   ${commandLine(first, config.isWindows)}`}</Text>
          {after ? <Text wrap="wrap">{`  then   ${after}`}</Text> : null}
          {actions(confirm('Update'), skip, cancel)}
          {howTo}
        </Box>
      )
    }
    if (items.length > 1) {
      const shown = items.length > 6 ? items.slice(0, 5) : items
      const kindsIn = new Set(items.map(i => i.kind))
      const skips = [...sorted.broken.map(i => `${i.name} (known issue)`), ...sorted.info.map(i => `${i.name} (info)`)]
      return (
        <Box flexDirection="column">
          <Text weight="bold" wrap="wrap">
            Update {items.length} items?
          </Text>
          {shown.map((i, n) => (
            <Text wrap="wrap">{`  ${n + 1} ${commandLine(i, config.isWindows)}`}</Text>
          ))}
          {items.length > shown.length ? <Text dimColor>{`  + ${items.length - shown.length} more`}</Text> : null}
          {kindsIn.has('plugin') ? <Text wrap="wrap">{`  then   ${AFTER.plugin.all}`}</Text> : null}
          {kindsIn.has('skill') ? <Text wrap="wrap">{`  then   ${AFTER.skill}`}</Text> : null}
          {skips.length ? <Text wrap="wrap">{`  skips  ${skips.join(', ')}`}</Text> : null}
          {actions(confirm(`Update ${items.length}`), cancel)}
          {howTo}
        </Box>
      )
    }
    // The items left the list (a re-check finished): fall through to it.
  }

  if (check) {
    return (
      <Box flexDirection="column">
        {heading(`checking… ${elapsed(now - check.startedAt)}`)}
        <Text dimColor wrap="truncate-end">
          {`  ${kinds.map(k => SCOPE[k]).join(', ')} · ${check.isFetch ? 'fetching metadata' : 'no network fetch'}`}
        </Text>
        {result ? (
          <Box marginTop={1} flexDirection="column">
            <Text dimColor>{`  last check ${when(result.checkedAt, now)}`}</Text>
            {sorted.updatable.map(plainRow)}
          </Box>
        ) : null}
        {actions(close)}
      </Box>
    )
  }

  if (checkError) {
    return (
      <Box flexDirection="column">
        {heading('check failed')}
        <Text wrap="wrap">
          {'  '}
          <Text color="error">✗</Text> {checkError.text}
        </Text>
        {checkError.hint ? (
          <Text dimColor wrap="wrap">
            {`    ${checkError.hint}`}
          </Text>
        ) : null}
        {actions(checkNow, close)}
      </Box>
    )
  }

  if (!result) {
    return (
      <Box flexDirection="column">
        {heading('not checked yet')}
        {actions(checkNow, close)}
      </Box>
    )
  }

  const n = sorted.updatable.length
  return (
    <Box flexDirection="column">
      {heading(`${n ? `${n} available` : 'all current'} · checked ${when(result.checkedAt, now)}`)}
      {problemRows()}
      {n === 0 && result.checked.length ? (
        <Text wrap="wrap">
          {'  '}
          <Text color="success">✓</Text> {result.checked.join(' · ')}
        </Text>
      ) : null}
      {groups()}
      {actions(
        n ? <Button key="update-all" plain hotkey="a" label={`Update all (${n})`} onPress={() => confirmFor(sorted.updatable.map(keyOf))} /> : null,
        checkNow,
        close,
      )}
    </Box>
  )
}

/** The band row: one row or nothing, above whatever the rest of the band chain drew. */
async function bandRow($: Engine, e: RenderInput<'AbovePrompt'>) {
  const { Box, Text, Button } = $.ui.resolve(e)
  const result = await read($, resultA)
  const hiddenFor = await read($, hiddenA)
  const run = await read($, runA)
  const where = await read($, whereA) // subscribes the band to opens and closes
  await read($, nowA)
  const w = e.props.bodyColumns
  // `$.ui.panes()` is a call, not a subscription; `where` above is what redraws the band.
  if (where === 'pane') {
    const panes = await $.ui.panes()
    if (panes.some(p => p.id === PANE && p.isShown)) return null
  }
  const show = <Button key="show" plain label="Show" onPress={() => void openPane($)} />
  if (run && isRunning(run)) {
    const current = run.steps.findIndex(s => s.status === 'running')
    const now = await $.clock.now()
    return (
      <Box flexDirection="row" gap={2} width={w}>
        <Text wrap="truncate-end">
          <Text dimColor>…</Text> Updating {current + 1} of {run.steps.length} · {run.steps[current]?.name ?? ''} · {elapsed(now - run.startedAt)}
        </Text>
        <Box flexGrow={1} />
        {show}
      </Box>
    )
  }
  // No list yet and the check failed (no Python, say): the band says what to do, as a new session would see nothing.
  const failed = await read($, checkErrorA)
  if (!result && failed)
    return (
      <Box flexDirection="row" gap={2} width={w}>
        <Text wrap="truncate-end">
          <Text color="error">✗</Text> Uptick check failed · {failed.hint || failed.text}
        </Text>
        <Box flexGrow={1} />
        {show}
      </Box>
    )
  const sorted = sortItems(result, config.kinds)
  const { updatable } = sorted
  const sig = signature(updatable)
  if (!result || !updatable.length || hiddenFor === sig) return null
  const hide = async () => {
    await update($, hiddenA, () => sig)
    await $.store.set('hiddenFor', sig)
  }
  const n = updatable.length
  const now = await $.clock.now()
  const checkedAt = Date.parse(result.checkedAt)
  const problems = sorted.problems.filter(p => p.offline !== true).length
  // Drawn only from 64 columns; below, the row is the count and the buttons.
  const tail =
    w < 64
      ? []
      : [
          ...config.kinds.map(k => [GROUP[k], updatable.filter(i => i.kind === k).length] as const).filter(([, c]) => c > 0).map(([g, c]) => `${g} ${c}`),
          ...(problems ? [`? ${problems}`] : []),
          ...(hasOffline(sorted.problems) ? ['offline'] : []),
          ...(now - checkedAt > DAY ? [`checked ${when(result.checkedAt, now).slice(0, 5)}`] : []),
        ]
  return (
    <Box flexDirection="row" gap={2} width={w}>
      <Text wrap="truncate-end">
        <Text color="warning">↑</Text> {n}
        {w < 40 ? '' : ` tool update${n === 1 ? '' : 's'}`}
        {tail.length ? <Text dimColor>{` · ${tail.join(' · ')}`}</Text> : null}
      </Text>
      <Box flexGrow={1} />
      {show}
      {w < 40 ? null : <Button key="hide" plain dimColor label="Hide" onPress={() => void hide()} />}
    </Box>
  )
}

async function onSessionStart($: Engine, e: Args<'session.start'>, next: Next<'session.start'>) {
  config.isInteractive = e.isInteractive
  // A reload (or a /config change) drops the old module's work in flight while $.state keeps its marks:
  // clear them, or the pane would wait on a check or a run that no longer exists.
  const reloadedAt = await $.clock.now()
  await update($, checkA, () => null)
  await update($, runA, run =>
    run && isRunning(run)
      ? {
          ...run,
          endedAt: reloadedAt,
          steps: run.steps.map(s =>
            s.status === 'running'
              ? { ...s, status: 'failed' as const, errorLine: 'interrupted by a plugin reload' }
              : s.status === 'queued'
                ? { ...s, status: 'skipped' as const }
                : s,
          ),
        }
      : run,
  )
  await $.command.register({
    name: COMMAND,
    description: `${TITLE}: show updates for Claude Code, plugins, skills, Homebrew, uv tools and winget; run one after you confirm it`,
  })
  // After the command is registered, so a refused env read cannot cost /uptick; read once here, not in draw,
  // where a failing env read would break the pane. A refused read leaves the rest of the start as it is.
  try {
    const onWindows = await isWindows($)
    config.isWindows = onWindows
  } catch (err) {
    $.ui.log(`uptick: environment not read: ${message(err)}`, { to: 'debug' })
  }
  if (config.isInteractive) {
    try {
      const cached = await readCache($)
      if (cached) await publish($, cached)
      const hidden = await $.store.get('hiddenFor')
      if (typeof hidden === 'string') await update($, hiddenA, () => hidden)
      await checkIfDue($, cached)
    } catch (err) {
      $.ui.log(`uptick: cache not read: ${message(err)}`, { to: 'debug' })
    }
    cacheTimer?.cancel()
    cacheTimer = $.clock.every(FIFTEEN_MINUTES, () => void refreshFromCache($))
  }
  return next(e)
}

// Answers /uptick only: any other command passes on untouched.
async function onCommandRun($: Engine, e: Args<'command.run'>, next: Next<'command.run'>) {
  if (e.command !== COMMAND) return next(e)
  if (!config.isInteractive) {
    // Headless: a read-only report, nothing is ever run.
    const out = await runChecker($, false)
    if ('error' in out) return { text: `${TITLE}: check failed: ${out.error.text}\n  ${out.error.hint}`, exitCode: 1 }
    const now = await $.clock.now()
    const onWindows = await isWindows($)
    return { text: report(out.result, config.kinds, true, now, onWindows) }
  }
  const cached = await read($, resultA)
  const run = await read($, runA)
  const isChecking = !isRunning(run) // openPane starts a check unless a run goes on
  await openPane($)
  const now = await $.clock.now()
  const where = await read($, whereA)
  // One line: the list is in the pane, so the conversation does not carry it.
  const head = report(cached, config.kinds, false, now).split('\n')[0]
  return { text: `${head}; ${isChecking ? 'checking now, ' : ''}the list is ${where === 'band' ? 'above the prompt' : 'in the pane'}` }
}

// The person's close mark closes the pane without our Close: the band must learn of it too.
async function onClose($: Engine, e: Args<'ui.close'>, next: Next<'ui.close'>) {
  if (e.id === PANE) await update($, whereA, () => null)
  return next(e)
}

// The band is shared with other mods' rows: always keep what next(e) drew, ours on top.
async function onBand($: Engine, e: RenderInput<'AbovePrompt'>, next: Next<'ui.render', RenderInput<'AbovePrompt'>>) {
  if (e.props.hasSurvey) return next(e)
  const where = await read($, whereA)
  const isFallback = where === 'band'
  // The row stays while Claude works, so Show can open the pane mid-turn.
  if (!isFallback && config.options.hint !== 'band') return next(e)
  const mine = isFallback ? await draw($, e, true) : await bandRow($, e)
  if (!mine) return next(e)
  // next(e) rejects only when nothing answers beneath (a test without an engine hook): ours still draws.
  const below = await next(e).catch(() => undefined)
  const { Box } = $.ui.resolve(e)
  return below ? (
    <Box flexDirection="column">
      {mine}
      {below}
    </Box>
  ) : (
    mine
  )
}

async function onPane($: Engine, e: RenderInput<'Pane'>, next: Next<'ui.render', RenderInput<'Pane'>>) {
  if (e.requestId !== PANE) return next(e)
  return draw($, e, false)
}

export const register: Register = (on, options) => {
  config.options = options
  config.kinds = parseKinds(options.kinds)
  on('session.start', onSessionStart)
  on('command.run', { command: COMMAND }, onCommandRun)
  on('ui.close', onClose)
  on('ui.render', { component: 'AbovePrompt' }, onBand)
  on('ui.render', { component: 'Pane', requestId: PANE }, onPane)
}
