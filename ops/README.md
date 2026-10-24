# ops

`runner_watchdog.sh` kicks the self-hosted Actions runner when a run is queued
>10 min while the runner is online/idle (stale session). Never touches a busy
runner; 30 min cooldown. Flags: `--dry-run`, `--simulate-queued`.
Env: `WATCHDOG_REPO`, `RUNNER_DIR`, `RUNNER_LABEL`, `RUNNER_NAME`,
`WATCHDOG_MIN_AGE_MINUTES`, `WATCHDOG_COOLDOWN_MINUTES`. Log: `~/Library/Logs/runner_watchdog.log`.

## Install
    cp ops/com.razs.runner-watchdog.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.razs.runner-watchdog.plist

## Uninstall
    launchctl bootout gui/$(id -u)/com.razs.runner-watchdog
    rm ~/Library/LaunchAgents/com.razs.runner-watchdog.plist
