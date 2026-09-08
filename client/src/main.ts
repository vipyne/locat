import "./style.css";

const connectButton = document.querySelector<HTMLButtonElement>("#connect")!;
const connectionState = document.querySelector<HTMLSpanElement>("#connection-state")!;

function setConnectionState(state: string): void {
  connectionState.textContent = state;
}

connectButton.addEventListener("click", () => {
  setConnectionState("transport not wired yet");
});

setConnectionState("disconnected");
