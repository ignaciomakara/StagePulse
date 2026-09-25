const stageId = decodeURIComponent(location.pathname.split("/").at(-1));
const connection = document.querySelector("#connection");
const captionElement = document.querySelector("#captions");
const languageSelect = document.querySelector("#language");
const histories = {
  en: { finalLines: [], preview: "" },
  es: { finalLines: [], preview: "" },
};
let socket = null;

function render() {
  captionElement.replaceChildren();
  const history = histories[languageSelect.value];
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
  socket.onopen = () => { connection.textContent = "Captions connected"; };
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
    connection.textContent = "Captions disconnected. Reconnecting…";
    setTimeout(connect, 1200);
  };
}

languageSelect.onchange = render;
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
  connection.textContent = `Stage unavailable: ${error.message}`;
}
