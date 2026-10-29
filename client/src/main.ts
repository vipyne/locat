import "./style.css";
import type { TransportState } from "@pipecat-ai/client-js";
import {
  attachBotAudio,
  createClient,
  pickTransport,
  preloadDailyCallMachine,
  startBot,
  transportPinned,
  type SessionMode,
} from "./connection";
import {
  attachConfigPanel,
  attachModeToggle,
  attachMoneyRain,
  attachOnlineBanner,
  attachRagPanel,
  attachStopwatch,
} from "./panels";
import { attachTranscript } from "./transcript";

const connectButton = document.querySelector<HTMLButtonElement>("#connect")!;
const modeSelect = document.querySelector<HTMLSelectElement>("#mode-select")!;
const muteButton = document.querySelector<HTMLButtonElement>("#mute")!;
const connectionState = document.querySelector<HTMLSpanElement>("#connection-state")!;
const transcriptPane = document.querySelector<HTMLElement>("#transcript")!;
const composer = document.querySelector<HTMLFormElement>("#composer")!;
const configLines = document.querySelector<HTMLPreElement>("#config-lines")!;
const ragTurns = document.querySelector<HTMLElement>("#rag-turns")!;
const textInput = document.querySelector<HTMLInputElement>("#text-input")!;
const sendButton = document.querySelector<HTMLButtonElement>("#send")!;

const IDLE_STATES: TransportState[] = [
  "disconnected",
  "initializing",
  "initialized",
  "error",
];

function render(state: TransportState): void {
  connectionState.textContent = state;
  const idle = IDLE_STATES.includes(state);
  connectButton.textContent = idle ? "Connect" : "Disconnect";
  muteButton.disabled = idle;
  textInput.disabled = idle;
  sendButton.disabled = idle;
  modeSelect.disabled = !idle;
}

function sessionMode(): SessionMode {
  return modeSelect.value === "online" ? "online" : "offline";
}

// Default offline; a valid ?mode= in the URL pre-selects the toggle so a
// shared link (or the fallback/mode reload below) survives.
const requestedMode = new URLSearchParams(window.location.search).get("mode");
if (requestedMode === "online") modeSelect.value = "online";

const transportName = pickTransport(sessionMode());

// Transport follows the mode, so flipping modes reloads the page onto the new
// mode's transport (the toggle is disabled while connected — always
// pre-connect). sessionStorage survives the reload, so the per-transport
// fallback flags carry over. A pinned ?transport= keeps its transport: record
// the mode in the URL in place.
modeSelect.addEventListener("change", () => {
  const url = new URL(window.location.href);
  url.searchParams.set("mode", sessionMode());
  if (transportPinned()) {
    window.history.replaceState(null, "", url);
    return;
  }
  url.searchParams.delete("autoconnect");
  window.location.assign(url);
});

document.querySelector<HTMLSpanElement>("#transport-name")!.textContent = transportName;

// Serve mode from the bot (/api/lofi-mode): grey out the option an
// --online-only / --offline-only process refuses and pre-select the one it
// serves. Forcing the value fires the change handler, so the page reloads
// once onto the right mode's transport.
async function applyServeMode(): Promise<void> {
  let mode = "both";
  try {
    const resp = await fetch("/api/lofi-mode");
    mode = (await resp.json()).mode ?? "both";
  } catch {
    return;
  }
  if (mode !== "online-only" && mode !== "offline-only") return;
  const forced = mode === "online-only" ? "online" : "offline";
  for (const option of modeSelect.options) {
    option.disabled = option.value !== forced;
  }
  if (modeSelect.value !== forced) {
    modeSelect.value = forced;
    modeSelect.dispatchEvent(new Event("change"));
  }
}
void applyServeMode();

// Connect failed: reload once onto websocket (pure TCP, crosses any boundary
// that forwards TCP — the Windows/WSL2 case) and auto-connect. Storage guards
// the loop; no storage → no auto-retry.
const FALLBACK_NEXT: Partial<Record<string, string>> = {
  webrtc: "websocket",
};

function fallBackToNextTransport(): boolean {
  const next = FALLBACK_NEXT[transportName];
  if (!next) return false;
  try {
    const key = `lofi-${transportName}-failed`;
    if (sessionStorage.getItem(key)) return false;
    sessionStorage.setItem(key, "1");
  } catch {
    return false;
  }
  connectionState.textContent = `${transportName} failed — retrying with ${next}…`;
  const url = new URL(window.location.href);
  url.searchParams.set("transport", next);
  url.searchParams.set("autoconnect", "1");
  window.location.assign(url);
  return true;
}

// Before the transport exists: it reuses whatever call object daily-js
// already holds, so the vendored-bundle override has to be set here.
const dailyReady = preloadDailyCallMachine(transportName);

const client = createClient(transportName, render);
attachBotAudio(client);
const transcript = attachTranscript(client, transcriptPane);
attachConfigPanel(client, configLines);
attachRagPanel(client, ragTurns);
attachMoneyRain(client, document.querySelector<HTMLElement>("#rag-panel")!);
attachModeToggle(client, document.querySelector<HTMLButtonElement>("#mode-toggle")!);
attachOnlineBanner(client, document.querySelector<HTMLElement>("#online-banner")!);
attachStopwatch(client, document.querySelector<HTMLElement>("#rag-stopwatch")!);

connectButton.addEventListener("click", async () => {
  connectButton.disabled = true;
  try {
    if (IDLE_STATES.includes(client.state)) {
      await dailyReady;
      await client.connect(await startBot(transportName, sessionMode()));
    } else {
      await client.disconnect();
    }
  } catch (error) {
    console.error("[lofi] connect failed:", error);
    if (fallBackToNextTransport()) return;
    connectionState.textContent = `error: ${error instanceof Error ? error.message : error}`;
  } finally {
    connectButton.disabled = false;
  }
});

muteButton.addEventListener("click", () => {
  client.enableMic(!client.isMicEnabled);
  muteButton.textContent = client.isMicEnabled ? "Mute" : "Unmute";
});

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!text) return;
  textInput.value = "";
  transcript.addUserText(text);
  try {
    await client.sendText(text);
  } catch (error) {
    connectionState.textContent = `error: ${error instanceof Error ? error.message : error}`;
  }
});

render(client.state);

// Arriving via the automatic transport fallback (webrtc→websocket): connect
// without a click — mic
// permission was already granted to this origin on the failed attempt.
if (new URLSearchParams(window.location.search).get("autoconnect") === "1") {
  connectButton.click();
}
