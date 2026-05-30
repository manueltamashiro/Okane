# Okane — Phase 6 operational setup (macOS launchd)

These `launchd` agents run the unattended pieces of a Phase 6 paper-trading
validation: the daily data refresh, the always-on trading loop, and weekly DB
pruning. They are **not** auto-installed — you copy them into
`~/Library/LaunchAgents/` and `launchctl load` them yourself.

| Plist | What it does | Schedule |
|---|---|---|
| `com.okane.pipeline.plist` | `python main.py pipeline` (ingest + indicators) | Daily 17:00 local |
| `com.okane.paper-trading.plist` | `python main.py paper_trading 60`, restarts on crash | Continuous (KeepAlive) |
| `com.okane.prune.plist` | `python main.py prune 90` (trim old equity snapshots) | Sunday 18:00 local |

## Before you start

1. **Paths are hard-coded.** Every plist assumes the checkout is at
   `/Users/manueltamashiro/ai-server/Okane` and the venv at `venv/`. If yours
   differs, edit the `<string>` paths in each plist (interpreter, `main.py`,
   `WorkingDirectory`, and the three log paths).

2. **Validate first.** Run each command in the foreground once and confirm it
   behaves before handing it to launchd:
   ```bash
   source venv/bin/activate
   python main.py pipeline        # should ingest + compute indicators
   python main.py paper_trading   # should start the loop (Ctrl+C to stop)
   python main.py prune 90        # should report N snapshots pruned
   ```

3. **Telegram is required for paper trading.** `paper_trading` refuses to start
   unless `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set to real values in
   `.env` (the `.env.example` placeholders are rejected). For an unattended run
   you want real credentials so halts and fills are not silent. To bypass anyway
   (alerts go only to the log), uncomment the `OKANE_ALLOW_NO_NOTIFIER` block in
   `com.okane.paper-trading.plist`.

## Install

```bash
cd /Users/manueltamashiro/ai-server/Okane

# Copy the agents you want
cp scripts/launchd/com.okane.pipeline.plist       ~/Library/LaunchAgents/
cp scripts/launchd/com.okane.prune.plist          ~/Library/LaunchAgents/
cp scripts/launchd/com.okane.paper-trading.plist  ~/Library/LaunchAgents/

# Load them (starts paper-trading immediately; pipeline/prune wait for schedule)
launchctl load ~/Library/LaunchAgents/com.okane.pipeline.plist
launchctl load ~/Library/LaunchAgents/com.okane.prune.plist
launchctl load ~/Library/LaunchAgents/com.okane.paper-trading.plist
```

## Verify

```bash
# Confirm they are registered (PID column is non-zero for the running loop)
launchctl list | grep com.okane

# Inspect a specific agent
launchctl list com.okane.paper-trading

# Tail the logs
tail -f logs/launchd-paper-trading.log
tail -f logs/launchd-pipeline.log
tail -f logs/launchd-prune.log
```

## Stop / uninstall

```bash
# Stop and unregister (paper-trading receives SIGTERM and exits after the
# current poll completes)
launchctl unload ~/Library/LaunchAgents/com.okane.paper-trading.plist
launchctl unload ~/Library/LaunchAgents/com.okane.pipeline.plist
launchctl unload ~/Library/LaunchAgents/com.okane.prune.plist

# Remove the files if you are done with them
rm ~/Library/LaunchAgents/com.okane.*.plist
```

## Notes

- **Logs.** Each agent writes combined stdout/stderr to `logs/launchd-*.log`.
  The app's own loguru file sink (`logs/trader_bot.log`, DEBUG+, 10 MB rotation,
  30-day retention) is separate and still active.
- **Why a separate prune job?** The paper-trading loop snapshots equity every
  poll (~1440/day at 60s). Without pruning, `trader_bot.db` grows unbounded over
  a multi-month run. 90 days of retention is enough to recompute peak equity and
  weekly P&L windows on restart.
- **Sleep/wake.** `StartCalendarInterval` jobs that are missed while the Mac is
  asleep run once on wake. If the machine is regularly asleep at 17:00, consider
  `pmset` wake schedules or running on an always-on host.
