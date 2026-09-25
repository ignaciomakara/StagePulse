import { initI18n, t } from "/static/i18n.js";

const stageId = decodeURIComponent(location.pathname.split("/").at(-1));
const connection = document.querySelector("#connection");
const captionElement = document.querySelector("#captions");
const languageSelect = document.querySelector("#language");
const histories = {
  en: { finalLines: [], preview: "" },
  es: { finalLines: [], preview: "" },
};
let socket = null;
let connectionMessage = { key: "connecting", values: {} };

function setConnection(key, values = {}) {
  connectionMessage = { key, values };
  connection.textContent = t(key, values);
}

function render() {
  captionElement.replaceChildren();
  const history = histories[languageSelect.value];
  if (!history.finalLines.length && !history.preview) {
    const line = document.createElement("p");
    line.textContent = t("waitingForCaptions");
    captionElement.append(line);
    return;
  }
  for (const text of history.finalLines) {
    const line = document.createElement("p");
    line.textContent = text;
    captionElement.append(line);
  }
  if (history.preview) {
    const line = document.createElement("p");
    line.className = "preview";
    line.textContent = history.preview;
    captionElement.append(line);
  }
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws/stages/${encodeURIComponent(stageId)}/captions`);
  socket.onopen = () => { setConnection("captionsConnected"); };
  socket.onmessage = ({ data }) => {
    const event = JSON.parse(data);
    if (!(event.language in histories)) return;
    const history = histories[event.language];
    if (event.is_final) {
      history.finalLines.push(event.text);
      if (history.finalLines.length > 5) history.finalLines.shift();
      history.preview = "";
    } else {
      history.preview = event.text;
    }
    render();
  };
  socket.onclose = () => {
    setConnection("captionsDisconnected");
    setTimeout(connect, 1200);
  };
}

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
  connect();
} catch (error) {
  setConnection("stageUnavailable", { error: error.message });
}
