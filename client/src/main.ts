import "./style.css";
import type { TransportState } from "@pipecat-ai/client-js";
import { createClient, startBot } from "./connection";
import { attachConfigPanel, attachRagPanel } from "./panels";
import { attachTranscript } from "./transcript";

const connectButton = document.querySelector<HTMLButtonElement>("#connect")!;
const muteButton = document.querySelector<HTMLButtonElement>("#mute")!;
const webtransportBanner = document.querySelector<HTMLDivElement>("#webtransport-banner")!;
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
}

const client = createClient(render);
const transcript = attachTranscript(client, transcriptPane);
attachConfigPanel(client, configLines);
attachRagPanel(client, ragTurns);

connectButton.addEventListener("click", async () => {
  connectButton.disabled = true;
  try {
    if (IDLE_STATES.includes(client.state)) {
      await client.connect(await startBot());
    } else {
      await client.disconnect();
    }
  } catch (error) {
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

if (typeof WebTransport === "undefined") {
  webtransportBanner.hidden = false;
  connectButton.disabled = true;
}

render(client.state);
