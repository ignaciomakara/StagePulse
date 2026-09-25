import { initI18n, t } from "/static/i18n.js";

const stageId = decodeURIComponent(location.pathname.split("/").at(-1));
const requested = new URLSearchParams(location.search).get("lang");
const mode = ["original", "es", "both"].includes(requested) ? requested : "both";
const status = document.querySelector("#display-status");
const captions = {
  en: document.querySelector("#display-original .display-text"),
  es: document.querySelector("#display-spanish .display-text"),
};
const latest = { en: null, es: null };
let socket = null;
let reconnectTimer = null;
let stageReady = false;
let connectionKey = "connecting";
let connectionValues = {};

document.querySelector("#display-original").hidden = mode === "es";
document.querySelector("#display-spanish").hidden = mode === "original";
document.querySelector("#display-captions").dataset.mode = mode;

function render() {
  status.textContent = t(connectionKey, connectionValues);
  status.dataset.state = connectionKey === "captionsConnected" ? "connected"
    : connectionKey === "stageUnavailable" ? "error" : "waiting";
  for (const language of ["en", "es"]) {
    const event = latest[language];
    captions[language].textContent = event ? event.text : t(
      connectionKey === "captionsReconnecting" ? "captionsReconnecting" : "waitingForCaptions"
    );
    captions[language].classList.toggle("preview", Boolean(event && !event.is_final));
    captions[language].classList.toggle("waiting", !event);
  }
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (socket) {
    connectionKey = "captionsReconnecting";
    latest.en = null;
    latest.es = null;
    render();
  }
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const current = new WebSocket(`${protocol}://${location.host}/ws/stages/${encodeURIComponent(stageId)}/captions`);
  socket = current;
  current.onopen = () => {
    if (socket !== current) return;
    connectionKey = "captionsConnected";
    render();
  };
  current.onmessage = ({ data }) => {
    if (socket !== current) return;
    const event = JSON.parse(data);
    if (!(event.language in latest)) return;
    latest[event.language] = event;
    render();
  };
  current.onclose = () => {
    if (socket !== current) return;
    connectionKey = "captionsReconnecting";
    latest.en = null;
    latest.es = null;
    render();
    reconnectTimer = setTimeout(connect, 1200);
  };
}

function reconnectOnReturn() {
  if (stageReady && document.visibilityState === "visible") connect();
}

document.addEventListener("visibilitychange", reconnectOnReturn);
window.addEventListener("pageshow", reconnectOnReturn);
window.addEventListener("online", reconnectOnReturn);
initI18n(render);
try {
  const response = await fetch(`/api/stages/${encodeURIComponent(stageId)}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const stage = await response.json();
  document.querySelector("#display-stage").textContent = stage.name;
  stageReady = true;
  connect();
} catch (error) {
  connectionKey = "stageUnavailable";
  connectionValues = { error: error.message };
  render();
}
