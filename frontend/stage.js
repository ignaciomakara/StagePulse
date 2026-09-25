import { createCaptionView } from "/static/captions.js";
import { initI18n, stateText, t } from "/static/i18n.js";
import { talkPrep } from "/static/talk-prep.js";

const stageSelect = document.querySelector("#stage");
const deviceSelect = document.querySelector("#device");
const sourceModeSelect = document.querySelector("#source-mode");
const liveInputControls = document.querySelector("#live-input-controls");
const testFileControls = document.querySelector("#test-file-controls");
const testFileInput = document.querySelector("#test-file-input");
const testFileName = document.querySelector("#test-file-name");
const devicesButton = document.querySelector("#devices");
const connection = document.querySelector("#connection");
const stageStatus = document.querySelector("#stage-status");
const stageBadge = document.querySelector("#stage-badge");
const audioSignal = document.querySelector("#signal-audio");
const providerSignal = document.querySelector("#signal-provider");
const translationSignal = document.querySelector("#signal-translation");
const viewerSignal = document.querySelector("#signal-viewers");
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
let fileStarting = false;
let fileRunning = false;
let stageActive = false;
let statusReady = false;
let sampleBuffer = [];
let samplePosition = 0;
let pcmBuffer = [];
let connectionMessage = { key: "idle", values: {} };
let audienceMessage = null;

function setConnection(key, values = {}) {
  connectionMessage = { key, values };
  connection.textContent = t(key, values);
}

function setAudienceMessage(key, values = {}) {
  audienceMessage = { key, values };
  audienceWarning.textContent = t(key, values);
}

const wsUrl = (path) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
const selectedStage = () => stageSelect.value;

function syncSourceControls() {
  const testFileMode = sourceModeSelect.value === "file";
  const locked = !statusReady || stageActive || desired || starting || fileStarting;
  liveInputControls.hidden = testFileMode;
  testFileControls.hidden = !testFileMode;
  deviceSelect.disabled = testFileMode || locked;
  devicesButton.disabled = testFileMode || locked;
  testFileInput.disabled = locked || fileRunning;
  document.querySelector("#choose-test-file").disabled = locked || fileRunning;
  sourceModeSelect.disabled = locked || fileRunning;
  stageSelect.disabled = desired || starting || fileStarting;
  startButton.disabled = locked || fileRunning
    || (testFileMode && !testFileInput.files.length);
  stopButton.disabled = fileStarting || !(desired || fileRunning);
}

function fileErrorKey(status) {
  return { 400: "emptyTestFile", 409: "stageBusy", 413: "testFileTooLarge", 415: "unsupportedTestFile" }[status]
    || "testFileStartFailed";
}

async function startTestFile() {
  const file = testFileInput.files[0];
  if (!file || fileStarting || fileRunning || !selectedStage()) return;
  fileStarting = true;
  syncSourceControls();
  setConnection("uploadingTestFile");
  try {
    const url = `/api/stages/${encodeURIComponent(selectedStage())}/test-file?filename=${encodeURIComponent(file.name)}`;
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
    if (!response.ok) {
      setConnection(fileErrorKey(response.status), { status: response.status });
      return;
    }
    fileRunning = true;
    stageActive = true;
    setConnection("testFileStreaming");
  } catch (_error) {
    setConnection("testFileNetworkError");
  } finally {
    fileStarting = false;
    syncSourceControls();
    updateStatus();
  }
}

async function stopTestFile() {
  if (!fileRunning || fileStarting) return;
  fileStarting = true;
  syncSourceControls();
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(selectedStage())}/stop`, { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    fileRunning = false;
    stageActive = false;
    setConnection("stageStopped");
  } catch (error) {
    setConnection("stopFailed", { error: error.message });
  } finally {
    fileStarting = false;
    syncSourceControls();
    updateStatus();
  }
}

async function refreshDevices() {
  const selected = deviceSelect.value || localStorage.getItem("stagepulse-device") || "";
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((item) => item.kind === "audioinput");
  deviceSelect.replaceChildren();
  for (const [index, item] of devices.entries()) {
    const option = document.createElement("option");
    option.value = item.deviceId;
    option.textContent = item.label || t("audioInputNumber", { number: index + 1 });
    deviceSelect.append(option);
  }
  if (devices.some((item) => item.deviceId === selected)) deviceSelect.value = selected;
}

async function enableDevices() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    stream.getTracks().forEach((track) => track.stop());
    await refreshDevices();
    setConnection("audioDevicesAvailable");
  } catch (error) {
    setConnection("audioPermissionError", { error: error.message });
  }
}

function sendPcmFrame(samples) {
  if (!audioSocket || audioSocket.readyState !== WebSocket.OPEN) return;
  if (audioSocket.bufferedAmount > 128_000) {
    setConnection("audioLag");
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
  setConnection("audioConnecting");
  const socket = new WebSocket(wsUrl(`/ws/stages/${encodeURIComponent(selectedStage())}/audio`));
  audioSocket = socket;
  socket.onopen = () => { setConnection("audioWaiting"); };
  socket.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (data.type === "ready") {
      setConnection(data.resumed ? "audioReconnected" : "audioStreaming");
    } else if (data.type === "error") {
      setConnection("stageError", { error: data.detail });
      desired = false;
      sessionStorage.removeItem("stagepulse-active");
    }
  };
  socket.onclose = (event) => {
    if (audioSocket === socket) audioSocket = null;
    if (desired) {
      setConnection("audioDisconnected", { reason: event.reason || event.code });
      reconnectTimer = setTimeout(connectAudio, 1200);
    }
  };
  socket.onerror = () => { setConnection("audioConnectionError"); };
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
      throw new Error(t("secureContextRequired"));
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
        setConnection("audioDeviceDisconnected");
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
    sessionStorage.setItem("stagepulse-stage", selectedStage());
    localStorage.setItem("stagepulse-device", deviceId);
    sessionStorage.setItem("stagepulse-active", "1");
    await refreshDevices();
    connectAudio();
    syncSourceControls();
  } catch (error) {
    setConnection("captureError", { error: error.message });
    await releaseCapture();
    startButton.disabled = false;
  } finally {
    starting = false;
    syncSourceControls();
  }
}

async function stopCapture() {
  desired = false;
  sessionStorage.removeItem("stagepulse-active");
  clearTimeout(reconnectTimer);
  if (audioSocket) audioSocket.close();
  audioSocket = null;
  await releaseCapture();
  stageSelect.disabled = false;
  startButton.disabled = false;
  stopButton.disabled = true;
  syncSourceControls();
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(selectedStage())}/stop`, { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    setConnection("stageStopped");
  } catch (error) {
    setConnection("stopFailed", { error: error.message });
  }
}

async function updateAudienceLink() {
  const stageId = selectedStage();
  if (!stageId) return;
  audienceLink.href = `/audience/${encodeURIComponent(stageId)}`;
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(stageId)}/audience-link`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const link = await response.json();
    if (stageId !== selectedStage()) return;
    audienceUrl.value = link.url;
    audienceQr.src = `/api/stages/${encodeURIComponent(stageId)}/audience-qr.svg`;
    setAudienceMessage(link.local_only ? "audienceLocalWarning" : "audienceShare");
  } catch (error) {
    if (stageId !== selectedStage()) return;
    setAudienceMessage("audienceLinkUnavailable", { error: error.message });
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
    if (generation !== captionGeneration) return;
    const event = JSON.parse(data);
    views[event.language]?.accept(event);
  };
  socket.onclose = () => {
    if (generation === captionGeneration) {
      setTimeout(() => {
        if (generation === captionGeneration) connectCaptions();
      }, 1200);
    }
  };
}

async function updateStatus() {
  const stageId = selectedStage();
  if (!stageId) return;
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(stageId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    if (stageId !== selectedStage()) return;
    statusReady = true;
    stageActive = ["starting", "running"].includes(status.state);
    talkPrep.setActive(stageActive);
    if (!desired && !fileStarting && status.source_mode === "test_file"
        && ["starting", "running"].includes(status.state) && !fileRunning) {
      sourceModeSelect.value = "file";
      fileRunning = true;
      setConnection("testFileStreaming");
      syncSourceControls();
    } else if (fileRunning && ["completed", "failed", "stopped"].includes(status.state)) {
      fileRunning = false;
      setConnection(status.state === "completed" ? "testFileCompleted"
        : status.state === "failed" ? "testFileFailed" : "stageStopped");
    }
    syncSourceControls();
    stageBadge.textContent = stateText(status.state);
    stageBadge.dataset.state = status.state;
    audioSignal.textContent = t(status.audio_receiving ? "yes" : "no");
    audioSignal.dataset.state = status.audio_receiving ? "connected" : "waiting";
    providerSignal.textContent = t(status.provider_connected ? "yes" : "no");
    providerSignal.dataset.state = status.provider_connected ? "connected" : "waiting";
    translationSignal.textContent = status.translation_status
      ? t(status.translation_status === "delayed" ? "translationDelayed" : "translationOk") : "—";
    translationSignal.dataset.state = status.translation_status || "waiting";
    viewerSignal.textContent = String(status.viewers);
    stageStatus.textContent = t("stageStatus", {
      state: stateText(status.state),
      connections: status.connections,
      viewers: status.viewers,
      error: status.error ? t("stageStatusError", { error: status.error }) : "",
    });
  } catch (error) {
    if (stageId !== selectedStage()) return;
    stageStatus.textContent = t("stageStatusUnavailable", { error: error.message });
  }
}

stageSelect.onchange = (event) => {
  if (event) sessionStorage.setItem("stagepulse-stage", selectedStage());
  stageActive = false;
  statusReady = false;
  fileRunning = false;
  sourceModeSelect.value = "live";
  testFileInput.value = "";
  testFileName.dataset.i18n = "noFileSelected";
  testFileName.textContent = t("noFileSelected");
  setConnection("idle");
  stageBadge.textContent = stateText("created");
  stageBadge.dataset.state = "created";
  stageStatus.textContent = t("stageStatusInitial");
  for (const signal of [audioSignal, providerSignal, translationSignal]) {
    signal.textContent = "\u2014";
    signal.dataset.state = "waiting";
  }
  viewerSignal.textContent = "\u2014";
  audienceLink.removeAttribute("href");
  audienceUrl.value = "";
  audienceQr.removeAttribute("src");
  audienceMessage = null;
  audienceWarning.textContent = "";
  syncSourceControls();
  updateAudienceLink();
  talkPrep.loadStage(selectedStage());
  views.en.clear();
  views.es.clear();
  connectCaptions();
  updateStatus();
};
deviceSelect.onchange = () => localStorage.setItem("stagepulse-device", deviceSelect.value);
devicesButton.onclick = enableDevices;
sourceModeSelect.onchange = syncSourceControls;
document.querySelector("#choose-test-file").onclick = () => testFileInput.click();
testFileInput.onchange = () => {
  const file = testFileInput.files[0];
  if (file) {
    testFileName.removeAttribute("data-i18n");
    testFileName.textContent = file.name;
  } else {
    testFileName.dataset.i18n = "noFileSelected";
    testFileName.textContent = t("noFileSelected");
  }
  syncSourceControls();
};
startButton.onclick = () => {
  talkPrep.setActive(true);
  return sourceModeSelect.value === "file" ? startTestFile() : startCapture();
};
stopButton.onclick = () => fileRunning ? stopTestFile() : stopCapture();
document.querySelector("#copy-audience-url").onclick = async () => {
  try {
    await navigator.clipboard.writeText(audienceUrl.value);
    setAudienceMessage("audienceLinkCopied");
  } catch (error) {
    setAudienceMessage("copyFailed", { error: error.message });
  }
};

initI18n(() => {
  setConnection(connectionMessage.key, connectionMessage.values);
  if (audienceMessage) setAudienceMessage(audienceMessage.key, audienceMessage.values);
  talkPrep.refreshLanguage();
  stageStatus.textContent = t("stageStatusInitial");
  updateStatus();
});
syncSourceControls();

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
  const saved = sessionStorage.getItem("stagepulse-stage");
  const requested = new URLSearchParams(location.search).get("stage");
  if (stages.some((stage) => stage.stage_id === requested)) stageSelect.value = requested;
  else if (stages.some((stage) => stage.stage_id === saved)) stageSelect.value = saved;
  const resumeActiveStage = sessionStorage.getItem("stagepulse-active") === "1" && selectedStage() === saved;
  stageSelect.onchange();
  await refreshDevices();
  setInterval(updateStatus, 1000);
  if (resumeActiveStage) startCapture();
} catch (error) {
  setConnection("cannotLoadStages", { error: error.message });
}
