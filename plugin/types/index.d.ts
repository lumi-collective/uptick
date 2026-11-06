/** One row of the checker's JSON: something with a newer version, or a note about one. */
export type ToolItem = {
  id: string
  /** plugin | skill | brew | uv (the mod may update these), self | info (shown only), broken (a note) */
  kind: string
  name: string
  have: string
  latest: string
  note?: string
  detail?: string
  cask?: boolean
  /** Known vulnerabilities of what this update replaces (opt-in scan): a count and the worst level. */
  vulns?: { count: number; level: string }
}

export type Problem = { text: string; hint?: string; offline?: boolean }

/** The checker's whole JSON output, as the cache file holds it. */
export type CheckResult = {
  checkedAt: string
  checked: string[]
  skipped: string[]
  items: ToolItem[]
  problems: Problem[]
}

export type Check = { startedAt: number; isFetch: boolean }

/** A check that gave no result: what happened, and what to do about it. */
export type CheckError = { text: string; hint: string }

/** `ids` are item keys (`kind:id`, `:cask` for a cask). */
export type View = { name: 'list' } | { name: 'confirm'; ids: string[] } | { name: 'result' } | { name: 'output' }

export type Step = {
  /** The item's key: `kind:id`, `:cask` for a cask. */
  key: string
  name: string
  kind: string
  have: string
  latest: string
  argv: string[]
  status: 'queued' | 'running' | 'ok' | 'failed' | 'skipped'
  exitCode?: number
  errorLine?: string
  log?: string
}

export type Run = { steps: Step[]; startedAt: number; endedAt?: number; isStopping: boolean }

declare module 'claude-code' {
  interface PluginState {
    'uptick': {
      result: CheckResult | null
      check: Check | null
      checkError: CheckError | null
      view: View
      run: Run | null
      now: number
      hiddenFor: string | null
      /** Where the pane is drawn: its own pane, the band (an open that was not placed), or closed. */
      where: 'pane' | 'band' | null
    }
  }
}
