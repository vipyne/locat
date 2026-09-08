import { RTVIEvent, type PipecatClient } from "@pipecat-ai/client-js";

export const CONFIG_MESSAGE_TYPE = "locat-config";

interface ModelEntry {
  role: string;
  model: string;
  path: string;
}

interface LocatConfig {
  type: string;
  models: ModelEntry[];
  ollama_host: string;
  ollama_started_by_locat: boolean;
  rag: string;
}

const OWNERSHIP = {
  locat: "started by locat (./locat.sh stop stops it)",
  foreign: "NOT started by locat (your own instance; ./locat.sh stop leaves it alone)",
};

export function attachConfigPanel(client: PipecatClient, panel: HTMLElement): void {
  client.on(RTVIEvent.ServerMessage, (data: unknown) => {
    const message = data as Partial<LocatConfig> | null;
    if (message?.type !== CONFIG_MESSAGE_TYPE) return;
    const lines = (message.models ?? []).flatMap((entry) => [
      `${entry.role.padEnd(7)}${entry.model}`,
      `       ${entry.path}`,
    ]);
    lines.push(
      `OLLAMA ${message.ollama_host}`,
      `       ${message.ollama_started_by_locat ? OWNERSHIP.locat : OWNERSHIP.foreign}`,
      `RAG    ${message.rag}`,
    );
    panel.textContent = lines.join("\n");
  });
}
