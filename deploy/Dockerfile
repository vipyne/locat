# Pipecat Cloud agent image for the `locat` bot (cloud/demo variant).
#
# Build CONTEXT is the repo root (see deploy/pcc-deploy.toml [build]) so we can pull in
# the cross-platform modules the cloud bot reuses. Target arch is Linux ARM64 — the
# base image and Pipecat Cloud both require it.
FROM dailyco/pipecat-base:latest

# Install the Linux-safe dependency subset (NOT the repo's mlx-whisper/local extras).
COPY deploy/requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# The entry point. Pipecat Cloud's base image discovers `bot.py`'s async `bot()`.
COPY deploy/bot.py bot.py

# Cross-platform pieces reused from the local bot. NOTE: we do NOT copy config.py — it
# steers model caches into ./models/ for the local repo layout; the container uses the
# default HF cache and downloads Whisper/Kokoro weights on first (cold) start.
COPY spoken_text_filter.py spoken_text_filter.py
COPY prompts/ prompts/

# Optional cold-start optimization (uncomment to bake weights into the image instead of
# downloading them on the first session — bigger image, faster first response):
# COPY deploy/prefetch.py prefetch.py
# RUN python prefetch.py
