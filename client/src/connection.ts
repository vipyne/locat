import { PipecatClient, type TransportState } from "@pipecat-ai/client-js";
import { MoqTransport } from "@pipecat-ai/moq-transport";

const RELAY_URL_COMES_FROM_START_RESPONSE = "";

export function createClient(
  onState: (state: TransportState) => void,
): PipecatClient {
  return new PipecatClient({
    transport: new MoqTransport({
      relayUrl: RELAY_URL_COMES_FROM_START_RESPONSE,
    }),
    enableMic: true,
    callbacks: {
      onTransportStateChanged: onState,
    },
  });
}

export async function startBot(): Promise<unknown> {
  const response = await fetch("/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ transport: "moq" }),
  });
  if (!response.ok) {
    throw new Error(`/start failed (${response.status}): ${await response.text()}`);
  }
  return response.json();
}
