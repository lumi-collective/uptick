# AGENTS.md

Instructions for coding agents working on Uptick. The README says what it does for users.

## Layout

- `plugin/`: the plugin, and all an install gets. Nothing outside it may be needed at run time.
- `plugin/.claude-plugin/plugin.json`: the manifest, with the `userConfig` settings.
  `.claude-plugin/marketplace.json` (repo root): this repo as its own marketplace, `source: ./plugin`.
- `plugin/README.md`: the directory listing's text: where it works, what it runs, reads and sends. Keep it
  in step with the main README, text only: no images, and no file of the plugin named in a code block.
- `plugin/hooks/register.tsx`: the mod. Registers `/uptick`, draws the pane and the line above the prompt, starts
  the checker, runs confirmed updates.
- `plugin/hooks/logic.ts`: the pure parts: the argv templates and id patterns (`argvFor`), item keys, parsing the
  checker's JSON, the report text.
- `plugin/hooks/register.test.tsx`: the mod's tests (inside the plugin: `claude plugin test` loads the mod
  from the folder it is given), on Claude Code's plugin test kit. A fake engine; no real
  command runs.
- `plugin/types/index.d.ts`: the checker's JSON types and the mod's state.
- `plugin/checker/check_updates.py`: the checker. Python 3.9+, standard library only. Read-only.
- `plugin/bin/uptick-check`: the daily check (checker with `--fetch --cache`, a notification on Linux). Its `bin/`
  also keeps the directory listing to Claude Code.
- `tests/checker/test_check_updates.py`, `tests/checker/testdata/`: the checker's tests and synthetic
  fixtures, outside the plugin.
- `extras/`: companions outside Claude Code (schedulers, Raycast).
- `docs/reference.md`: the details behind the README (settings in depth, the checker's JSON, limits). The
  listing of runs, reads and network access stays in the README itself.

## Tests

Run all of them before you finish:

```
claude plugin validate plugin
claude plugin validate .claude-plugin/marketplace.json
claude plugin test plugin
python3 -I -B -m unittest discover -s tests/checker -p "test_*.py"
shellcheck plugin/bin/uptick-check extras/macos/raycast/uptick-check.sh
```

The checker must also pass on Python 3.9. Write the failing test first, then the fix. Do not weaken a test
to make it pass.

## Invariants

- **Plain mod API calls only** (the plugin directory's rule): every use of `$` is a plain
  `$.noun.method(...)` call whose result goes into a const before use (no parentheses around it, no cast,
  `!`, `?.` or brackets, no `.then`/`.catch` chained on it); `$` is passed whole, only to a function
  declared at the top level of the same file (`async function name($: Engine)`), or to `read`/`update`;
  each hook is such a function, registered with `on(...)` on a line of its own, takes `($, e, next)` and
  calls `return next(e)` for an event that is not its own. No `$.command.run`.
- **The listing tells everything.** A new program run, file read or network request is a change to
  `plugin/README.md` and the main README in the same commit.
- **Nothing runs without the confirm step.** An update starts only from the confirm step's Update button,
  which has no hotkey. The test world fails any update argv that runs before a confirm.
- **Argv only, no shell.** Every command is an argument list. The one exception: on Windows, `npx` and
  `claude` are `.cmd` shims, run as `cmd.exe /d /c …` (`onHost`); the id patterns admit none of cmd's
  special characters.
- **Id patterns gate every run.** An id comes from a command's output, so `argvFor` checks it against its
  kind's pattern (no leading dash, no spaces, no shell or path characters). A new kind gets a pattern and
  tests that refuse hostile ids.
- **Items are keyed `kind:id`, plus `:cask` for a cask.** Two items with one name each run their own
  command.
- **The network access is exactly what the README lists** under "What leaves your machine". A new request
  is a README change in the same commit.
- **Outside text is cleaned** before it reaches the pane (`clean`) or a terminal (the checker's `CONTROL`).
- **The cache schema only grows.** Add fields; never rename or remove one. Other tools read the file.
- **Kept in sync:** the skills CLI pin lives in `plugin/hooks/logic.ts` and `plugin/checker/check_updates.py`. A
  checker test fails when they differ.

## Style

Plain, short sentences in docs and comments. A comment says why, not what. No new dependencies.
