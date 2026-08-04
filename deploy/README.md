# Deploying `locat` to Pipecat Cloud

This directory is a **separate, cloud-only variant** of the bot. Your local offline bots
(`bot.py`, `bot_web.py`, `bot_moq.py`) are untouched — this is additive.

> ⚠️ **Read the privacy note first.** The local bot's whole point is "nothing leaves the
> machine." Pipecat Cloud runs the bot **in Daily's cloud**, so audio + transcripts transit
> their infra, and the LLM becomes a **networked endpoint**. Great for a **demo**; for your
> *private* financial use, keep running it locally. If you want private **and** remote, see
> "Keep it local instead" at the bottom.

## Why a cloud container forces three changes

Pipecat Cloud agents are **Linux ARM64, CPU, small** containers. So, vs. the local bot:

| Piece | Local | Cloud (here) | Why |
|---|---|---|---|
| Transport | `LocalAudioTransport` | **Daily** (`DailyParams`) | no mic/speaker in a container |
| STT | `WhisperSTTServiceMLX` | **faster-whisper** (`WhisperSTTService`) | MLX is Apple-Silicon-only; won't import on Linux |
| LLM | local Ollama | **remote** OpenAI-compatible endpoint | no GPU / 9 GB model in the container |
| TTS, VAD, Smart-Turn | Kokoro + Silero + Smart-Turn v3 | *unchanged* | all ONNX, cross-platform |

## The Ollama part — two ways to host the LLM

The bot points at `OLLAMA_BASE_URL` (an OpenAI-compatible `/v1` URL). Because
`OLLamaLLMService` is just `OpenAILLMService`, **no bot code changes** — only the secret.

### (A) Demo-friendly
- **vLLM on Modal** — Pipecat's documented pattern. `modal deploy` their `vllm_inference.py`
  example → you get `https://…modal.run/v1`. Scales to zero when idle.
- Or any GPU host (RunPod, Fly.io GPU) running `ollama serve` or vLLM.
- Or (privacy irrelevant for a demo) a hosted API — Groq, Together, OpenAI — zero infra.

### (B) Self-hosted — you control the model
1. Run `ollama serve` on **your own** GPU box (cloud GPU VM, RunPod, or a home GPU machine).
2. Secure it — Ollama has no auth of its own:
   - front it with a reverse proxy for **TLS + a bearer token**, **or**
   - expose it over a **Tailscale / Cloudflare Tunnel** so Pipecat Cloud reaches it with no
     open public ports.
3. Point the bot at it (same as A): `OLLAMA_BASE_URL=https://your-host/v1`, `OLLAMA_API_KEY=…`.

> Even in (B): the *model* is yours, but the bot + audio still run through Daily's cloud.

## Deploy steps

```bash
# 0. one-time: the Pipecat CLI
pip install "pipecat-ai[cli]"
pipecat cloud login

# 1. store your secrets (the LLM endpoint + Daily key live here, never in git)
pipecat cloud secrets create locat-secrets \
  --set OLLAMA_BASE_URL="https://your-llm-host/v1" \
  --set OLLAMA_API_KEY="…" \
  --set DAILY_API_KEY="…"

# 2. edit deploy/pcc-deploy.toml (agent_name, image, region) — TODOs are marked

# 3. deploy from the REPO ROOT (cloud build uses deploy/Dockerfile + repo-root context).
#    Copy the config to the root first (pcc-deploy.toml is read from the CWD):
cp deploy/pcc-deploy.toml ./pcc-deploy.toml
pipecat cloud deploy

# 4. Pipecat returns a session/connection URL (Daily WebRTC). Open it and talk.
```

## Test the LLM endpoint locally BEFORE deploying

The #1 failure is "my remote LLM isn't reachable / isn't OpenAI-compatible." Isolate it:

```bash
curl -sf "$OLLAMA_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OLLAMA_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:14b","messages":[{"role":"user","content":"pong?"}],"stream":false}'
```
A JSON reply here means the deployed bot will reach it too.

## Notes / gotchas

- **Cold start:** with `min_agents = 0`, the first session downloads the Whisper + Kokoro
  weights (adds latency). Bake them into the image (see the commented block in the
  Dockerfile) to avoid it.
- **STT latency:** faster-whisper on the CPU `agent-1x` profile is slow for big models —
  keep `WHISPER_MODEL` small (`base`/`small`), or run STT on your GPU host too.
- **Not tested here:** this scaffold is verified for structure, not run end-to-end — it needs
  your Pipecat Cloud account, a live LLM endpoint, and a Daily key.

## Keep it local instead (private + remote)

If the real goal is "reach my private bot from elsewhere without giving up privacy," Pipecat
Cloud is the wrong tool (audio transits Daily). Instead run `bot_web.py` / `bot_moq.py` on an
always-on box you own and reach it over **Tailscale** or a **Cloudflare Tunnel** — model,
audio, and transcripts never leave infrastructure you control.
