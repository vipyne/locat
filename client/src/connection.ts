// The transports' own pinned dependency (hoisted, one copy — so this import
// and theirs share daily-js's call-instance singleton).
import Daily from "@daily-co/daily-js";
import { PipecatClient, RTVIEvent, type TransportState } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { WebSocketTransport } from "@pipecat-ai/websocket-transport";

export type TransportName = "webrtc" | "websocket";
export type SessionMode = "offline" | "online";

const TRANSPORT_NAMES: TransportName[] = ["webrtc", "websocket"];

// True when ?transport= names a real transport. That pin outranks the session
// mode, so the mode toggle leaves the transport alone.
export function transportPinned(): boolean {
  const param = new URLSearchParams(window.location.search).get("transport");
  return TRANSPORT_NAMES.includes(param as TransportName);
}

// A pinned ?transport=webrtc|websocket wins; otherwise the session mode
// picks. Offline → websocket: pure TCP to localhost, the one transport that
// connects with wi-fi off (airgapped, WebRTC's ICE gathers no usable candidate
// — browsers exclude loopback and STUN is unreachable). It is also the only
// path across Windows↔WSL2, where UDP never crosses. Online → webrtc: a
// network exists by definition and UDP media over loopback is the
// lowest-latency path; a failed connect falls back once to websocket (see
// fallBackToNextTransport in main.ts), which costs ~15-30 s on WSL2, so pin
// ?transport=websocket there.
export function pickTransport(mode: SessionMode): TransportName {
  const param = new URLSearchParams(window.location.search).get("transport");
  if (TRANSPORT_NAMES.includes(param as TransportName)) return param as TransportName;
  return mode === "online" ? "webrtc" : "websocket";
}

// Directory bot_browser.py serves the vendored call-machine bundle from.
const DAILY_BUNDLE_DIR = "/daily";

// WebSocket and SmallWebRTC transports stand up a Daily call object for their
// mic/device plumbing (no Daily call involved), and daily-js lazy-loads its
// call-machine engine from c.daily.co at device init — airgapped that fetch
// burns a 20s timeout and the client never leaves "initializing". Create the
// call object here, pointed at the server's local copy, and LOAD it before
// the transports touch it: the override lives in `properties.dailyConfig`, and
// the transports' `startCamera({dailyConfig: …})` shallow-merges its own over
// ours, so it only survives if the bundle is loaded by then. The transports
// reuse this instance via `getCallInstance()`.
export function preloadDailyCallMachine(transportName: TransportName): Promise<void> {
  const callObject =
    Daily.getCallInstance() ??
    Daily.createCallObject({
      dailyConfig: {
        bundlePathOverride: new URL(DAILY_BUNDLE_DIR, window.location.origin).toString(),
      },
    });
  return callObject.load().catch((error: unknown) => {
    console.warn("[lofi] daily call-machine preload failed:", error);
  });
}

function buildTransport(transportName: TransportName) {
  if (transportName === "webrtc") return new SmallWebRTCTransport();
  return new WebSocketTransport();
}

export function createClient(
  transportName: TransportName,
  onState: (state: TransportState) => void,
): PipecatClient {
  return new PipecatClient({
    transport: buildTransport(transportName),
    enableMic: true,
    callbacks: {
      onTransportStateChanged: onState,
    },
  });
}

// SmallWebRTC delivers bot audio as a media track the page must attach and
// play; the websocket transport plays through its own audio pipeline
// and emit no track. Hidden autoplay element carries whichever track arrives
// (Connect click precedes the track, so autoplay-with-sound is permitted).
export function attachBotAudio(client: PipecatClient): void {
  const el = document.createElement("audio");
  el.autoplay = true;
  document.body.append(el);
  client.on(RTVIEvent.TrackStarted, (track, participant) => {
    if (participant?.local || track.kind !== "audio") return;
    el.srcObject = new MediaStream([track]);
  });
}

// Websocket sessions carry mode as `?mode=` on the connect URL itself (the
// bot reads it off the live `/ws-client` query params — there's no /start
// round trip to hand it a body); WebRTC carries it as `requestData.mode` in
// the /api/offer POST (query params on /api/offer never reach the bot).
function withMode(url: string, mode: SessionMode): string {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}mode=${mode}`;
}

export async function startBot(transportName: TransportName, mode: SessionMode): Promise<unknown> {
  // WebRTC sessions start when the offer hits /api/offer — no /start round
  // trip; hand the transport its offer endpoint directly.
  if (transportName === "webrtc") {
    return {
      webrtcRequestParams: {
        endpoint: new URL("/api/offer", window.location.origin).toString(),
        requestData: { mode },
      },
    };
  }
  const response = await fetch("/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ transport: transportName }),
  });
  if (!response.ok) {
    throw new Error(`/start failed (${response.status}): ${await response.text()}`);
  }
  const data = (await response.json()) as { wsUrl?: string; token?: string | null };
  // WebSocketTransport rejects unknown keys (the /start response carries
  // sessionId and a null token) — hand it exactly what it validates.
  if (transportName === "websocket") {
    const params: Record<string, string> = { wsUrl: withMode(data.wsUrl ?? "", mode) };
    if (data.token) params.token = data.token;
    return params;
  }
  return data;
}
