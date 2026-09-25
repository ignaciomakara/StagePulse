import { initI18n, stateText, t } from "/static/i18n.js";

const container = document.querySelector("#stages");
const controlStatus = document.querySelector("#control-status");
const cards = new Map();

function age(timestamp) {
  if (!timestamp) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(timestamp)) / 1000));
  if (!Number.isFinite(seconds)) return "—";
  return `${seconds}s`;
}

function connectionAge(seconds) {
  return seconds == null ? "—" : `${Math.floor(seconds)}s`;
}

function since(timestamp) {
  return timestamp ? t("ago", { age: age(timestamp) }) : "—";
}

const yesNo = (value) => t(value ? "yes" : "no");

function stateLabel(stage) {
  if (["failed", "stopped", "completed"].includes(stage.state)) return "stopped/error";
  if (stage.state === "running" && stage.provider_connected && stage.audio_receiving) return "running/connected";
  return "attention";
}

function addLink(parent, key, href) {
  const link = document.createElement("a");
  link.dataset.i18n = key;
  link.textContent = t(key);
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener";
  parent.append(link);
}

function createCard(stage) {
  const card = document.createElement("article");
  card.className = "stage-card";
  const heading = document.createElement("h2");
  const stageId = document.createElement("span");
  stageId.className = "stage-card-id";
  const title = document.createElement("div");
  title.append(heading, stageId);
  const state = document.createElement("p");
  state.className = "stage-indicator";
  const header = document.createElement("div");
  header.className = "stage-card-header";
  header.append(title, state);
  card.append(header);
  const details = document.createElement("dl");
  card.append(details);
  const actions = document.createElement("div");
  actions.className = "stage-actions";
  addLink(actions, "openStage", `/stage?stage=${encodeURIComponent(stage.stage_id)}`);
  addLink(actions, "openAudience", `/audience/${encodeURIComponent(stage.stage_id)}`);
  const overlayUrl = `/overlay/${encodeURIComponent(stage.stage_id)}?lang=original`;
  addLink(actions, "openOriginalOverlay", overlayUrl);
  addLink(actions, "openSpanishOverlay", `/overlay/${encodeURIComponent(stage.stage_id)}?lang=es`);
  const copy = document.createElement("button");
  copy.type = "button";
  copy.dataset.i18n = "copyOverlay";
  copy.textContent = t("copyOverlay");
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(new URL(overlayUrl, location.href).href);
      copy.dataset.i18n = "copied";
      copy.textContent = t("copied");
    } catch (error) {
      copy.textContent = t("copyFailed", { error: error.message });
    }
  };
  actions.append(copy);
  card.append(actions);
  container.append(card);
  cards.set(stage.stage_id, { card, heading, stageId, state, details });
  return cards.get(stage.stage_id);
}

function renderStage(stage) {
  const entry = cards.get(stage.stage_id) || createCard(stage);
  entry.heading.textContent = stage.name;
  entry.stageId.textContent = stage.stage_id;
  const label = stateLabel(stage);
  entry.state.textContent = t(label === "running/connected" ? "runningConnected" : label === "stopped/error" ? "stoppedError" : "attention");
  entry.state.dataset.state = label;
  const values = [
    ...(stage.source_mode && ["starting", "running"].includes(stage.state)
      ? [["audioSource", t(stage.source_mode === "test_file" ? "testFile" : "liveInput")]] : []),
    ["browserConnected", yesNo(stage.browser_connected)],
    ["audioReceiving", `${yesNo(stage.audio_receiving)} · ${t("lastAudio", { age: since(stage.last_audio_at) })}`],
    ["providerConnected", `${yesNo(stage.provider_connected)} · ${stateText(stage.provider_status)}`],
    ["translation", stage.translation_status
      ? t(stage.translation_status === "delayed" ? "translationDelayed" : "translationOk") : "—"],
    ["viewers", stage.viewers],
    ["connections", stage.connection_count],
    ["reconnects", stage.reconnect_count],
    ["sessionAge", connectionAge(stage.session_age_seconds)],
    ["connectionAge", connectionAge(stage.connection_age_seconds)],
    ["lastCaption", since(stage.last_caption_at)],
    ...(stage.last_error || stage.error ? [["lastError", stage.last_error || stage.error]] : []),
  ];
  entry.details.replaceChildren();
  for (const [key, value] of values) {
    const term = document.createElement("dt");
    term.dataset.key = key;
    term.textContent = t(key);
    const description = document.createElement("dd");
    description.dataset.key = key;
    if (key === "translation") description.dataset.state = stage.translation_status || "waiting";
    if (key === "browserConnected") description.dataset.state = stage.browser_connected ? "connected" : "waiting";
    if (key === "audioReceiving") description.dataset.state = stage.audio_receiving ? "connected" : "waiting";
    if (key === "providerConnected") description.dataset.state = stage.provider_connected ? "connected" : "waiting";
    description.textContent = String(value);
    entry.details.append(term, description);
  }
}

async function refresh() {
  try {
    const response = await fetch("/api/stages", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const stages = await response.json();
    const configured = new Set(stages.map((stage) => stage.stage_id));
    for (const [stageId, entry] of cards) {
      if (!configured.has(stageId)) {
        entry.card.remove();
        cards.delete(stageId);
      }
    }
    stages.forEach(renderStage);
    controlStatus.textContent = t("configuredStages", { count: stages.length, time: new Date().toLocaleTimeString() });
  } catch (error) {
    controlStatus.textContent = t("stageStatusUnavailable", { error: error.message });
  }
}

initI18n(refresh);
setInterval(refresh, 1000);
