import {
  RTVIEvent,
  type BotOutputData,
  type PipecatClient,
  type TranscriptData,
} from "@pipecat-ai/client-js";

const SCROLL_PIN_THRESHOLD_PX = 40;

// Dollar amounts and numbers, digit form ($1,234.56, 40.00, 2026); never a
// trailing comma or period.
const MONEY_RE = /-?\$?\d(?:[\d,]*\d)?(?:\.\d+)?/g;

// Render text with amounts/numbers wrapped in <span class="money"> — text
// nodes only, never innerHTML.
function renderHighlighted(el: HTMLElement, text: string): void {
  el.replaceChildren();
  let last = 0;
  for (const match of text.matchAll(MONEY_RE)) {
    const index = match.index ?? 0;
    if (index > last) el.append(text.slice(last, index));
    const span = document.createElement("span");
    span.className = "money";
    span.textContent = match[0];
    el.append(span);
    last = index + match[0].length;
  }
  if (last < text.length) el.append(text.slice(last));
}

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
      renderHighlighted(pendingUserText, data.text);
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
    // Keep only the message that opens a segment: the pre-synthesis LLM text, in
    // its own digit form. Drops the post-synthesis duplicate ("completed") and
    // the word-level progress messages ("in-progress"), which both carry
    // SpokenTextFilter's words-form numbers.
    if (data.spoken_status !== undefined) {
      if (data.spoken_status !== "new") return;
    } else if (data.spoken === true) {
      return;
    }
    keepScrolled(() => {
      botText ??= addTurn("bot");
      const key =
        data.segment_id !== undefined
          ? `segment-${data.segment_id}`
          : `unkeyed-${unkeyedSegmentCount++}`;
      botSegments.set(key, data.text);
      renderHighlighted(botText, [...botSegments.values()].join(" "));
    });
  });

  return {
    addUserText(text: string): void {
      keepScrolled(() => {
        renderHighlighted(addTurn("user"), text);
      });
    },
  };
}
