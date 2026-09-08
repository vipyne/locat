import "./style.css";
import type { TransportState } from "@pipecat-ai/client-js";
import { createClient, startBot } from "./connection";
import { attachTranscript } from "./transcript";

const connectButton = document.querySelector<HTMLButtonElement>("#connect")!;
const connectionState = document.querySelector<HTMLSpanElement>("#connection-state")!;
const transcriptPane = document.querySelector<HTMLElement>("#transcript")!;

const IDLE_STATES: TransportState[] = [
  "disconnected",
  "initializing",
  "initialized",
  "error",
];

function render(state: TransportState): void {
  connectionState.textContent = state;
  connectButton.textContent = IDLE_STATES.includes(state) ? "Connect" : "Disconnect";
}

const client = createClient(render);
attachTranscript(client, transcriptPane);

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

render(client.state);
