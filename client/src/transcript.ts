import {
  RTVIEvent,
  type BotOutputData,
  type PipecatClient,
  type TranscriptData,
} from "@pipecat-ai/client-js";

const SCROLL_PIN_THRESHOLD_PX = 40;

export interface Transcript {
  addUserText(text: string): void;
}

export function attachTranscript(client: PipecatClient, pane: HTMLElement): Transcript {
  let pendingUserText: HTMLElement | null = null;
  let botText: HTMLElement | null = null;
  let botSegments = new Map<string, string>();
  let unkeyedSegmentCount = 0;

  function pinned(): boolean {
    return pane.scrollHeight - pane.scrollTop - pane.clientHeight < SCROLL_PIN_THRESHOLD_PX;
  }

  function keepScrolled(update: () => void): void {
    const wasPinned = pinned();
    update();
    if (wasPinned) pane.scrollTop = pane.scrollHeight;
  }

  function addTurn(role: "user" | "bot"): HTMLElement {
    const turn = document.createElement("div");
    turn.className = `turn ${role}`;
    const speaker = document.createElement("span");
    speaker.className = "speaker";
    speaker.textContent = role === "user" ? "you" : "bot";
    const text = document.createElement("span");
    text.className = "text";
    turn.append(speaker, text);
    pane.append(turn);
    return text;
  }

  client.on(RTVIEvent.UserTranscript, (data: TranscriptData) => {
    keepScrolled(() => {
      pendingUserText ??= addTurn("user");
      pendingUserText.textContent = data.text;
      pendingUserText.classList.toggle("partial", !data.final);
      if (data.final) pendingUserText = null;
    });
  });

  client.on(RTVIEvent.BotLlmStarted, () => {
    botText = null;
    botSegments = new Map();
    unkeyedSegmentCount = 0;
  });

  client.on(RTVIEvent.BotOutput, (data: BotOutputData) => {
    keepScrolled(() => {
      botText ??= addTurn("bot");
      const key =
        data.segment_id !== undefined
          ? `segment-${data.segment_id}`
          : `unkeyed-${unkeyedSegmentCount++}`;
      botSegments.set(key, data.text);
      botText.textContent = [...botSegments.values()].join(" ");
    });
  });

  return {
    addUserText(text: string): void {
      keepScrolled(() => {
        addTurn("user").textContent = text;
      });
    },
  };
}
