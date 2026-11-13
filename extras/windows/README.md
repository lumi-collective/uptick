# Daily check on Windows: Task Scheduler

The checker refreshes the cache the mod reads, `%USERPROFILE%\.cache\uptick\last.json`. With a clone of this
repository at `%USERPROFILE%\src\uptick`, in a Command Prompt:

```
schtasks /Create /SC DAILY /ST 09:00 /TN "Uptick check" /TR "py -3 -I -X utf8 \"%USERPROFILE%\src\uptick\plugin\checker\check_updates.py\" --fetch --cache --known-broken-from-config"
```

`%USERPROFILE%` is expanded when the task is created; the `\"` quotes keep a path with a space whole.
`--known-broken-from-config` reads the `knownBroken` you set in `/config`. Add the other options you set there to
the end of the `/TR` command, for example `--kinds=plugin,skill` or `--extra=C:\path\my-check.exe`.

Run it once now: `schtasks /Run /TN "Uptick check"`. Remove it: `schtasks /Delete /TN "Uptick check"`.

There is no notification; `/uptick` shows the result.
