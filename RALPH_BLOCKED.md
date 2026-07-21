# Handoff to a human — remaining steps for locat v1

All **autonomous** build phases (0–5) are complete and verified. What's left needs a
human at a real machine — a live microphone, speakers, and the ability to toggle
Wi-Fi. The autonomous loop cannot do these and must not fake them, so it stopped here.

Do these in order, in a **normal terminal** (not a headless/sandboxed one).

## 0. Prerequisites (one-time)

```bash
brew install portaudio
uv sync
uv run python scripts/prefetch_models.py   # already run once online; re-run only if ./models/huggingface or ./models/kokoro is missing
```

## 1. Pull the LLM into the repo's Ollama store  (Phase 2, second half)

The loop could not run the `ollama` binary (permission-gated headless), so this was
never done. `scripts/run_ollama.sh` is correct — just run it:

```bash
bash scripts/run_ollama.sh
```

This relocates Ollama's store to `./models/ollama`, starts `ollama serve`, and pulls
`qwen2.5:14b` (~9 GB). Leave it running (foreground) — it's the local LLM server the
bot talks to. Confirm with:

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama list   # should show qwen2.5:14b
```

## 2. Online smoke test  (Phase 6, step 1)

In another terminal, with the Ollama server from step 1 still running:

```bash
uv run bot.py
```

- You should hear the spoken greeting, then be able to talk and hear replies.
- **Confirm interruptions / barge-in:** talk over the bot mid-sentence — it should
  stop and yield to you.

> Note flagged during the build (PROGRESS.md, Phase 3 pipeline-assembly entry): the
> Pipecat 1.0 migration guide says `TransportParams.vad_analyzer` / `turn_analyzer`
> moved onto the user aggregator. Our `build_transport()` still passes them to
> `LocalAudioTransportParams` and it constructs fine on 1.5.0, but whether barge-in
> actually works through the local transport can only be settled by this live test.
> If interruptions don't work, move VAD/turn config onto `LLMUserAggregatorParams`.

## 3. Verify config changes visibly take effect  (Phase 4 verify)

While iterating, edit `.env` and confirm behavior changes:
- Change `LLM_MODEL` (e.g. to `qwen2.5:7b`, pull it first with
  `LLM_MODEL=qwen2.5:7b bash scripts/run_ollama.sh`) → different answers/latency.
- Change `KOKORO_VOICE` (e.g. `af_bella`, `am_michael`) → the voice you hear changes.

## 4. THE OFFLINE TEST — v1's definition of done  (Phase 6, steps 2–3)

1. Keep the local Ollama server running (step 1).
2. **Turn off Wi-Fi / enable Airplane Mode.**
3. `uv run bot.py` and hold a full spoken conversation.
4. With `LOG_LEVEL=DEBUG` (the default), watch the logs and confirm **no service
   reaches the network** — no download or HTTP attempts after the warm-up.

If you can hold a real conversation with Wi-Fi off and see zero network activity,
**v1 is done.** ✅

---

When these pass, delete `RALPH_BLOCKED`, `RALPH_BLOCKED.md`, and `RALPH_DONE`, and
check off Phase 6 in `PROGRESS.md`.
