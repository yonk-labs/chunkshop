# cron + systemd timer scheduling

Simplest possible deployment: one Linux box, no orchestrator. Pick
**either** cron OR systemd timer — both files below are mutually
exclusive deploys.

## cron

Drop [`chunkshop-memory.cron`](chunkshop-memory.cron) into
`/etc/cron.d/`. It runs:

- **realtime** every minute
- **consolidate** nightly at 02:30
- **prune** weekly on Sunday at 03:00 (keeps only consolidated rows
  older than 30 days)

```bash
sudo cp chunkshop-memory.cron /etc/cron.d/chunkshop-memory
sudo chmod 644 /etc/cron.d/chunkshop-memory
# Edit the DSN line in the file to match your environment.
```

cron writes logs to `/var/log/chunkshop-*.log` — make sure the user
that runs the jobs (`app` in the file) can write there, or change the
path.

## systemd timer

Cleaner than cron for production — survives reboots, has structured
logs via `journalctl`, no PATH gotchas. Four files:

```bash
sudo cp chunkshop-memory-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chunkshop-memory-realtime.timer
sudo systemctl enable --now chunkshop-memory-consolidate.timer
# Check status:
systemctl list-timers | grep chunkshop
journalctl -u chunkshop-memory-realtime --since "10 min ago"
```

Edit `EnvironmentFile=` in each service file to point at your secret
location, e.g. `/etc/chunkshop/env` with:

```bash
CHUNKSHOP_MEMORY_DSN=postgresql://app:secret@db.internal:5432/agent_memory
```

`AccuracySec=5s` and `Persistent=true` mean the timer catches up after
downtime — if the box was off for 10 minutes, the next start triggers
one realtime run immediately, not ten. That's by design: the realtime
cell is idempotent and would otherwise pile up. The consolidate cell's
`Persistent=true` is more important — if you missed last night's run,
it should fire the next time the timer activates.
