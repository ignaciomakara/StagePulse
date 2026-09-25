import { createCaptionView } from "/static/captions.js";

const stageSelect = document.querySelector("#stage");
const deviceSelect = document.querySelector("#device");
const connection = document.querySelector("#connection");
const stageStatus = document.querySelector("#stage-status");
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");
const audienceLink = document.querySelector("#audience-link");
const audienceUrl = document.querySelector("#audience-url");
const audienceQr = document.querySelector("#audience-qr");
const audienceWarning = document.querySelector("#audience-warning");
const views = {
  en: createCaptionView(document.querySelector("#original"), "en"),
  es: createCaptionView(document.querySelector("#translated"), "es"),
};

let audioSocket = null;
let captionSocket = null;
let captionGeneration = 0;
let reconnectTimer = null;
let capture = null;
let desired = false;
let starting = false;
let sampleBuffer = [];
let samplePosition = 0;
let pcmBuffer = [];

const wsUrl = (path) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
const selectedStage = () => stageSelect.value;

async function refreshDevices() {
  const selected = deviceSelect.value || localStorage.getItem("stagepulse-device") || "";
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((item) => item.kind === "audioinput");
  deviceSelect.replaceChildren();
  for (const [index, item] of devices.entries()) {
    const option = document.createElement("option");
    option.value = item.deviceId;
    option.textContent = item.label || `Audio input ${index + 1}`;
    deviceSelect.append(option);
  }
  if (devices.some((item) => item.deviceId === selected)) deviceSelect.value = selected;
}

async function enableDevices() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    stream.getTracks().forEach((track) => track.stop());
    await refreshDevices();
    connection.textContent = "Audio devices available. Select an input, then Start.";
  } catch (error) {
    connection.textContent = `Audio permission/device error: ${error.message}`;
  }
}

function sendPcmFrame(samples) {
  if (!audioSocket || audioSocket.readyState !== WebSocket.OPEN) return;
  if (audioSocket.bufferedAmount > 128_000) {
    connection.textContent = "Audio connection is falling behind; dropping frames.";
    return;
  }
  const buffer = new ArrayBuffer(samples.length * 2);
  const pcm = new DataView(buffer);
  for (let index = 0; index < samples.length; index++) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    const value = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
    pcm.setInt16(index * 2, value, true);
  }
  audioSocket.send(buffer);
}

function acceptSamples(samples) {
  for (const sample of samples) sampleBuffer.push(sample);
  const ratio = capture.context.sampleRate / 16_000;
  while (samplePosition + 1 < sampleBuffer.length) {
    const index = Math.floor(samplePosition);
    const fraction = samplePosition - index;
    pcmBuffer.push(sampleBuffer[index] * (1 - fraction) + sampleBuffer[index + 1] * fraction);
    if (pcmBuffer.length === 1600) {
      sendPcmFrame(pcmBuffer);
      pcmBuffer = [];
    }
    samplePosition += ratio;
  }
  const consumed = Math.min(Math.floor(samplePosition), Math.max(0, sampleBuffer.length - 1));
  sampleBuffer.splice(0, consumed);
  samplePosition -= consumed;
}

function connectAudio() {
  if (!desired || audioSocket) return;
  connection.textContent = "Connecting audio to stage…";
  const socket = new WebSocket(wsUrl(`/ws/stages/${encodeURIComponent(selectedStage())}/audio`));
  audioSocket = socket;
  socket.onopen = () => { connection.textContent = "Audio connected; waiting for Gemini…"; };
  socket.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (data.type === "ready") {
      connection.textContent = data.resumed ? "Audio reconnected to active stage." : "Audio streaming to stage.";
    } else if (data.type === "error") {
      connection.textContent = `Stage error: ${data.detail}`;
      desired = false;
      localStorage.removeItem("stagepulse-active");
    }
  };
  socket.onclose = (event) => {
    if (audioSocket === socket) audioSocket = null;
    if (desired) {
      connection.textContent = `Audio disconnected (${event.reason || event.code}). Reconnecting…`;
      reconnectTimer = setTimeout(connectAudio, 1200);
    }
  };
  socket.onerror = () => { connection.textContent = "Audio connection error."; };
}

async function releaseCapture() {
  if (!capture) return;
  capture.stream.getTracks().forEach((track) => track.stop());
  await capture.context.close();
  capture = null;
  sampleBuffer = [];
  samplePosition = 0;
  pcmBuffer = [];
}

async function startCapture() {
  if (desired || starting || !selectedStage()) return;
  starting = true;
  startButton.disabled = true;
  try {
    if (!navigator.mediaDevices || !window.AudioWorkletNode) {
      throw new Error("A secure context and AudioWorklet support are required (use localhost or HTTPS). ");
    }
    const deviceId = deviceSelect.value;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
      video: false,
    });
    for (const track of stream.getAudioTracks()) {
      track.onended = () => {
        connection.textContent = "Audio device disconnected.";
        stopCapture();
      };
    }
    const context = new AudioContext();
    capture = { stream, context };
    await context.audioWorklet.addModule("/static/audio-worklet.js");
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context, "stagepulse-capture");
    worklet.port.onmessage = ({ data }) => acceptSamples(data);
    source.connect(worklet);
    worklet.connect(context.destination);
    await context.resume();
    desired = true;
    stopButton.disabled = false;
    stageSelect.disabled = true;
    localStorage.setItem("stagepulse-stage", selectedStage());
    localStorage.setItem("stagepulse-device", deviceId);
    localStorage.setItem("stagepulse-active", "1");
    await refreshDevices();
    connectAudio();
  } catch (error) {
    connection.textContent = `Capture error: ${error.message}`;
    await releaseCapture();
    startButton.disabled = false;
  } finally {
    starting = false;
  }
}

async function stopCapture() {
  desired = false;
  localStorage.removeItem("stagepulse-active");
  clearTimeout(reconnectTimer);
  if (audioSocket) audioSocket.close();
  audioSocket = null;
  await releaseCapture();
  stageSelect.disabled = false;
  startButton.disabled = false;
  stopButton.disabled = true;
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(selectedStage())}/stop`, { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    connection.textContent = "Stage stopped.";
  } catch (error) {
    connection.textContent = `Stop failed: ${error.message}`;
  }
}

async function updateAudienceLink() {
  const stageId = selectedStage();
  if (!stageId) return;
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(stageId)}/audience-link`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const link = await response.json();
    if (stageId !== selectedStage()) return;
    audienceLink.href = link.url;
    audienceUrl.value = link.url;
    audienceQr.src = `/api/stages/${encodeURIComponent(stageId)}/audience-qr.svg`;
    audienceWarning.textContent = link.local_only
      ? "This localhost QR cannot be opened from another device. Set STAGEPULSE_PUBLIC_BASE_URL to a reachable LAN or HTTPS origin."
      : "Share this QR with the audience.";
  } catch (error) {
    audienceWarning.textContent = `Audience link unavailable: ${error.message}`;
  }
}

function connectCaptions() {
  captionGeneration++;
  const generation = captionGeneration;
  if (captionSocket) captionSocket.close();
  if (!selectedStage()) return;
  const socket = new WebSocket(wsUrl(`/ws/stages/${encodeURIComponent(selectedStage())}/captions`));
  captionSocket = socket;
  socket.onmessage = ({ data }) => {
    const event = JSON.parse(data);
    views[event.language]?.accept(event);
  };
  socket.onclose = () => {
    if (generation === captionGeneration) setTimeout(connectCaptions, 1200);
  };
}

async function updateStatus() {
  if (!selectedStage()) return;
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(selectedStage())}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    stageStatus.textContent = `Stage: ${status.state} · Gemini connections opened: ${status.connections} · Caption viewers: ${status.viewers}${status.error ? ` · Error: ${status.error}` : ""}`;
  } catch (error) {
    stageStatus.textContent = `Stage status unavailable: ${error.message}`;
  }
}

stageSelect.onchange = (event) => {
  if (event) localStorage.setItem("stagepulse-stage", selectedStage());
  updateAudienceLink();
  views.en.clear();
  views.es.clear();
  connectCaptions();
  updateStatus();
};
deviceSelect.onchange = () => localStorage.setItem("stagepulse-device", deviceSelect.value);
document.querySelector("#devices").onclick = enableDevices;
startButton.onclick = startCapture;
stopButton.onclick = stopCapture;
document.querySelector("#copy-audience-url").onclick = async () => {
  try {
    await navigator.clipboard.writeText(audienceUrl.value);
    audienceWarning.textContent = "Audience link copied.";
  } catch (error) {
    audienceWarning.textContent = `Copy failed: ${error.message}`;
  }
};

try {
  const response = await fetch("/api/stages");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const stages = await response.json();
  for (const stage of stages) {
    const option = document.createElement("option");
    option.value = stage.stage_id;
    option.textContent = stage.name;
    stageSelect.append(option);
  }
  const saved = localStorage.getItem("stagepulse-stage");
  const requested = new URLSearchParams(location.search).get("stage");
  if (stages.some((stage) => stage.stage_id === requested)) stageSelect.value = requested;
  else if (stages.some((stage) => stage.stage_id === saved)) stageSelect.value = saved;
  const resumeActiveStage = localStorage.getItem("stagepulse-active") === "1" && selectedStage() === saved;
  stageSelect.onchange();
  await refreshDevices();
  setInterval(updateStatus, 1000);
  if (resumeActiveStage) startCapture();
} catch (error) {
  connection.textContent = `Cannot load stages: ${error.message}`;
}
