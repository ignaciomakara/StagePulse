const stageId = decodeURIComponent(location.pathname.split("/").at(-1));
const language = new URLSearchParams(location.search).get("lang") === "es" ? "es" : "en";
const caption = document.querySelector("#overlay-caption");
let socket;
let clearTimer;

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws/stages/${encodeURIComponent(stageId)}/captions`);
  socket.onmessage = ({ data }) => {
    const event = JSON.parse(data);
    if (event.language !== language) return;
    caption.textContent = event.text;
    caption.classList.toggle("preview", !event.is_final);
    clearTimeout(clearTimer);
    clearTimer = setTimeout(() => { caption.textContent = ""; }, 15_000);
  };
  socket.onclose = () => {
    setTimeout(connect, 1200);
  };
}

connect();
