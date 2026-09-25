import { initI18n, t } from "/static/i18n.js";

const stageId = decodeURIComponent(location.pathname.split("/").at(-1));
const connection = document.querySelector("#connection");
const captionElement = document.querySelector("#captions");
const languageSelect = document.querySelector("#language");
const MAX_RECENT_CAPTIONS = 3;
const histories = {
  en: { finalLines: [], preview: "" },
  es: { finalLines: [], preview: "" },
};
let socket = null;
let reconnectTimer = null;
let stageReady = false;
let connectionMessage = { key: "connecting", values: {} };

function setConnection(key, values = {}) {
  connectionMessage = { key, values };
  connection.textContent = t(key, values);
  connection.dataset.state = key === "captionsConnected" ? "connected"
    : key === "captionsReconnecting" ? "reconnecting"
      : key === "stageUnavailable" ? "error" : "waiting";
}

function render() {
  captionElement.replaceChildren();
  captionElement.dataset.state = connectionMessage.key === "captionsReconnecting"
    ? "reconnecting" : "connected";
  const history = histories[languageSelect.value];
  if (!history.finalLines.length && !history.preview) {
    const line = document.createElement("p");
    line.className = "empty-caption";
    line.textContent = t(connectionMessage.key === "captionsReconnecting"
      ? "captionsReconnecting" : "waitingForCaptions");
    captionElement.append(line);
    return;
  }
  const visibleFinals = history.preview ? history.finalLines.slice(-2) : history.finalLines;
  for (const [index, text] of visibleFinals.entries()) {
    const line = document.createElement("p");
    line.className = history.preview || index < visibleFinals.length - 1 ? "previous" : "current";
    line.textContent = text;
    captionElement.append(line);
  }
  if (history.preview) {
    const line = document.createElement("p");
    line.className = "current preview";
    line.textContent = history.preview;
    captionElement.append(line);
  }
}

function clearCaptions() {
  for (const history of Object.values(histories)) {
    history.finalLines.length = 0;
    history.preview = "";
  }
  render();
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (socket) {
    setConnection("captionsReconnecting");
    clearCaptions();
  }
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const current = new WebSocket(`${protocol}://${location.host}/ws/stages/${encodeURIComponent(stageId)}/captions`);
  socket = current;
  current.onopen = () => { if (socket === current) { setConnection("captionsConnected"); render(); } };
  current.onmessage = ({ data }) => {
    if (socket !== current) return;
    const event = JSON.parse(data);
    if (!(event.language in histories)) return;
    const history = histories[event.language];
    if (event.is_final) {
      history.finalLines.push(event.text);
      if (history.finalLines.length > MAX_RECENT_CAPTIONS) history.finalLines.shift();
      history.preview = "";
    } else {
      history.preview = event.text;
    }
    render();
  };
  current.onclose = () => {
    if (socket !== current) return;
    setConnection("captionsReconnecting");
    clearCaptions();
    reconnectTimer = setTimeout(connect, 1200);
  };
}

function reconnectOnReturn() {
  if (stageReady && document.visibilityState === "visible") connect();
}

document.addEventListener("visibilitychange", reconnectOnReturn);
window.addEventListener("pageshow", reconnectOnReturn);
window.addEventListener("online", reconnectOnReturn);

languageSelect.onchange = render;
initI18n(() => {
  setConnection(connectionMessage.key, connectionMessage.values);
  render();
});
document.querySelector("#fullscreen").onclick = async () => {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await document.documentElement.requestFullscreen();
};
try {
  const response = await fetch(`/api/stages/${encodeURIComponent(stageId)}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const stage = await response.json();
  document.querySelector("#stage-title").textContent = stage.name;
  stageReady = true;
  connect();
} catch (error) {
  setConnection("stageUnavailable", { error: error.message });
}
