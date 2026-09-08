import { RTVIEvent, type PipecatClient } from "@pipecat-ai/client-js";

export const CONFIG_MESSAGE_TYPE = "locat-config";
export const RAG_MESSAGE_TYPE = "locat-rag";

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

interface RagChunk {
  source_path: string;
  page: number | null;
  score: number;
  text: string;
}

interface LocatRag {
  type: string;
  query: string;
  chunks: RagChunk[];
}

function chunkLocation(chunk: RagChunk): string {
  const where = chunk.page === null ? chunk.source_path : `${chunk.source_path} p.${chunk.page}`;
  return `${where} (${chunk.score.toFixed(2)})`;
}

export function attachRagPanel(client: PipecatClient, panel: HTMLElement): void {
  client.on(RTVIEvent.ServerMessage, (data: unknown) => {
    const message = data as Partial<LocatRag> | null;
    if (message?.type !== RAG_MESSAGE_TYPE) return;
    const turn = document.createElement("div");
    turn.className = "rag-turn";
    const query = document.createElement("p");
    query.className = "rag-query";
    query.textContent = message.query ?? "";
    turn.append(query);
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
