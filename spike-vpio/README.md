# spike-vpio

Prototype code for the VPIO echo-cancellation spike (plan: `../SPIKE_VPIO.md`,
progress: `../SPIKE_PROGRESS.md`). All spike code and dependencies stay in here —
the main bot's venv and `bot.py` are untouched until Phase 3.

Spike-local environment (independent of the repo root `.venv`):

```sh
cd spike-vpio
uv venv          # creates spike-vpio/.venv
uv sync          # installs spike deps (none yet; added in Phase 1)
uv run python <probe>.py
```
