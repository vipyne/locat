import { RTVIEvent, type PipecatClient } from "@pipecat-ai/client-js";

export const CONFIG_MESSAGE_TYPE = "lofi-config";
export const RAG_MESSAGE_TYPE = "lofi-rag";
export const RAG_PENDING_MESSAGE_TYPE = "lofi-rag-pending";
export const MODE_MESSAGE_TYPE = "lofi-mode";
const BUSY_MESSAGE_TYPE = "lofi-busy";
const IDLE_MESSAGE_TYPE = "lofi-idle";

interface ModelEntry {
  role: string;
  model: string;
  path: string;
  missing?: boolean;
}

// Server flags a role whose weights resolve to nothing; the path line says
// how to get them instead of printing a path that isn't there.
const PREFETCH_CMD = "uv run python scripts/prefetch_models.py";

interface LofiConfig {
  type: string;
  models: ModelEntry[];
  ollama_host: string;
  ollama_started_by_lofi: boolean;
  rag: string;
  architecture?: string;
  mode?: string;
}

const OWNERSHIP = {
  lofi: "started by lofi (./lofi.sh stop stops it)",
  foreign: "NOT started by lofi (your own instance; ./lofi.sh stop leaves it alone)",
};

interface RagChunk {
  source: string;
  page: number | null;
  text: string;
}

interface RagRoute {
  tool: string;
  args: Record<string, unknown>;
  count: number;
  elapsed_ms: number;
}

interface LofiRag {
  type: string;
  tool: string;
  args: Record<string, unknown>;
  count: number;
  elapsed_ms: number;
  chunks: RagChunk[];
}

function chunkLocation(chunk: RagChunk): string {
  return chunk.page == null ? chunk.source : `${chunk.source} p.${chunk.page}`;
}

function routeLine(route: RagRoute): string {
  const args = JSON.stringify(route.args ?? {});
  return `${route.tool} ${args} \u00b7 ${route.count} rows \u00b7 ${Math.round(route.elapsed_ms)}ms`;
}

interface LofiRagPending {
  type: string;
  tool: string;
  args: Record<string, unknown>;
  active: boolean;
}

// An analyst lookup runs 10-70s; show it in flight so silence doesn't read as
// "nothing is happening". One call at a time, so one pending line.
function renderPending(panel: HTMLElement, message: LofiRagPending): void {
  clearPending(panel);
  if (!message.active) return;
  const turn = document.createElement("div");
  turn.className = "rag-turn rag-pending";
  const line = document.createElement("p");
  line.className = "rag-route";
  const args = JSON.stringify(message.args ?? {});
  line.textContent = `${message.tool} ${args === "{}" ? "" : args} \u00b7 working\u2026`;
  turn.append(line);
  panel.prepend(turn);
}

function clearPending(panel: HTMLElement): void {
  for (const node of panel.querySelectorAll(".rag-pending")) node.remove();
}

export function attachRagPanel(client: PipecatClient, panel: HTMLElement): void {
  client.on(RTVIEvent.ServerMessage, (data: unknown) => {
    const message = data as Partial<LofiRag> | null;
    if (message?.type === RAG_PENDING_MESSAGE_TYPE) {
      renderPending(panel, message as unknown as LofiRagPending);
      return;
    }
    if (message?.type !== RAG_MESSAGE_TYPE) return;
    clearPending(panel);
    const turn = document.createElement("div");
    turn.className = "rag-turn";
    const line = document.createElement("p");
    line.className = "rag-route";
    line.textContent = routeLine(message as unknown as RagRoute);
    turn.append(line);
    for (const chunk of message.chunks ?? []) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = chunkLocation(chunk);
      const text = document.createElement("p");
      text.className = "rag-text";
      text.textContent = chunk.text;
      details.append(summary, text);
      turn.append(details);
    }
    if (panel.querySelector(".rag-turn")) {
      panel.prepend(turn);
    } else {
      panel.replaceChildren(turn);
    }
  });
}

// Vaporwave color per model role (style.css .cfg-*); LLM is sea foam green.
const ROLE_CLASSES: Record<string, string> = {
  STT: "cfg-stt",
  LLM: "cfg-llm",
  ANALYST: "cfg-analyst",
  TTS: "cfg-tts",
  EMBED: "cfg-embed",
};

export function attachConfigPanel(client: PipecatClient, panel: HTMLElement): void {
  client.on(RTVIEvent.ServerMessage, (data: unknown) => {
    const message = data as Partial<LofiConfig> | null;
    if (message?.type !== CONFIG_MESSAGE_TYPE) return;
    panel.replaceChildren();
    const append = (text: string, className?: string) => {
      if (!className) {
        panel.append(text);
        return;
      }
      const span = document.createElement("span");
      span.className = className;
      span.textContent = text;
      panel.append(span);
    };
    for (const entry of message.models ?? []) {
      // "qwen2.5:7b (Ollama @ …)" → role + name colored, detail plain.
      const detailAt = entry.model.indexOf(" (");
      const name = detailAt === -1 ? entry.model : entry.model.slice(0, detailAt);
      const detail = detailAt === -1 ? "" : entry.model.slice(detailAt);
      append(`${entry.role.padEnd(8)}${name}`, ROLE_CLASSES[entry.role]);
      if (entry.missing) {
        append(`${detail}\n       `);
        append(`not downloaded — run: ${PREFETCH_CMD}`, "cfg-missing");
        append("\n");
      } else {
        append(`${detail}\n       ${entry.path}\n`);
      }
    }
    append(
      [
        `OLLAMA ${message.ollama_host}`,
        `       ${message.ollama_started_by_lofi ? OWNERSHIP.lofi : OWNERSHIP.foreign}`,
        `ARCH   ${message.architecture ?? "cascade"}`,
        `RAG    ${message.rag}`,
      ].join("\n"),
    );
  });
}

// lofi-config names the session's resolved mode (Task 7) — the banner
// reflects what the server confirmed, not what the client requested.
export function attachOnlineBanner(client: PipecatClient, banner: HTMLElement): void {
  client.on(RTVIEvent.ServerMessage, (data: unknown) => {
    const message = data as Partial<LofiConfig> | null;
    if (message?.type !== CONFIG_MESSAGE_TYPE) return;
    const online = message.mode === "online";
    banner.hidden = !online;
    if (!online) return;
    banner.replaceChildren("⚠️ ONLINE MODE — ");
    const detail = document.createElement("span");
    detail.className = "online-detail";
    detail.textContent =
      "conversation and retrieved public documents go to Deepgram, OpenAI, and Cartesia; the private records db is not mounted.";
    banner.append(detail);
  });
}

interface LofiMode {
  type: string;
  mode: "fast" | "accurate";
  analyst_model: string;
  thinking: boolean;
}

const MODE_LABELS = { fast: "⚡ fast", accurate: "\u{1F9E0} accurate" };

// Toggle the analyst between accurate (thinking) and fast (no-think). Hidden
// until a lofi-config message says the bot runs the observer architecture;
// button state renders only from the server's confirmation.
export function attachModeToggle(client: PipecatClient, button: HTMLButtonElement): void {
  let mode: LofiMode["mode"] = "accurate";
  button.textContent = MODE_LABELS[mode];

  button.addEventListener("click", () => {
    const next = mode === "accurate" ? "fast" : "accurate";
    client.sendClientMessage(MODE_MESSAGE_TYPE, { mode: next });
  });

  client.on(RTVIEvent.ServerMessage, (data: unknown) => {
    const message = data as Partial<LofiMode> & Partial<LofiConfig>;
    if (message?.type === CONFIG_MESSAGE_TYPE) {
      button.hidden = message.architecture !== "observer";
      return;
    }
    if (message?.type !== MODE_MESSAGE_TYPE || !message.mode) return;
    mode = message.mode;
    button.textContent = MODE_LABELS[mode];
    button.title = `analyst: ${message.analyst_model} · thinking ${message.thinking ? "on" : "off"}`;
  });
}

// Live stopwatch while a records lookup is in flight; freezes the final time
// so the audience sees what the analyst cost. Same lifecycle as the money
// rain: lofi-busy starts it, lofi-rag / lofi-idle stop it.
export function attachStopwatch(client: PipecatClient, el: HTMLElement): void {
  let interval: number | undefined;
  let startedAt = 0;

  function render(): void {
    el.textContent = `${((performance.now() - startedAt) / 1000).toFixed(1)}s`;
  }

  // Freeze on the FIRST route line only — several arrive per lookup, and a
  // stray one after idle must not resurrect a stale time.
  function stop(frozen: boolean): void {
    if (interval === undefined) return;
    window.clearInterval(interval);
    interval = undefined;
    if (frozen) {
      render();
      el.textContent = `analyst: ${el.textContent}`;
    } else {
      el.textContent = "";
    }
  }

  client.on(RTVIEvent.ServerMessage, (data: unknown) => {
    const type = (data as { type?: string } | null)?.type;
    if (type === BUSY_MESSAGE_TYPE) {
      startedAt = performance.now();
      window.clearInterval(interval);
      interval = window.setInterval(render, 100);
      render();
    } else if (type === RAG_MESSAGE_TYPE) {
      stop(true);
    } else if (type === IDLE_MESSAGE_TYPE) {
      stop(false);
    }
  });
}

const MONEY_RAIN_MS = 30_000;
const MONEY_DROPS = 18;
const MONEY_EMOJI = ["\u{1F4B8}", "\u{1FA99}", "\u{1F4B5}", "\u{1F4B0}"];

export function attachMoneyRain(client: PipecatClient, host: HTMLElement): void {
  let rain: HTMLElement | null = null;
  let timeout: number | undefined;

  function stop(): void {
    window.clearTimeout(timeout);
    rain?.remove();
    rain = null;
  }

  function start(): void {
    stop();
    rain = document.createElement("div");
    rain.className = "money-rain";
    rain.setAttribute("aria-hidden", "true");
    for (let i = 0; i < MONEY_DROPS; i++) {
      const drop = document.createElement("span");
      drop.className = "money-drop";
      drop.textContent = MONEY_EMOJI[i % MONEY_EMOJI.length];
      drop.style.left = `${Math.random() * 92}%`;
      drop.style.animationDelay = `${Math.random() * 2.4}s`;
      drop.style.animationDuration = `${2 + Math.random() * 2}s`;
      drop.style.fontSize = `${0.9 + Math.random() * 0.9}rem`;
      rain.append(drop);
    }
    host.append(rain);
    timeout = window.setTimeout(stop, MONEY_RAIN_MS);
  }

  client.on(RTVIEvent.ServerMessage, (data: unknown) => {
    const type = (data as { type?: string } | null)?.type;
    if (type === BUSY_MESSAGE_TYPE) start();
    // Route lines arrive when the backend finishes; BotOutput can't be the stop
    // signal because the filler phrase itself is bot output.
    if (type === RAG_MESSAGE_TYPE || type === IDLE_MESSAGE_TYPE) stop();
  });
}
