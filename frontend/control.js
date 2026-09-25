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
  return timestamp ? `${age(timestamp)} ago` : "—";
}

function stateLabel(stage) {
  if (["failed", "stopped", "completed"].includes(stage.state)) return "stopped/error";
  if (stage.state === "running" && stage.provider_connected && stage.audio_receiving) return "running/connected";
  return "attention";
}

function addLink(parent, label, href) {
  const link = document.createElement("a");
  link.textContent = label;
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener";
  parent.append(link);
}

function createCard(stage) {
  const card = document.createElement("article");
  card.className = "stage-card";
  const heading = document.createElement("h2");
  card.append(heading);
  const state = document.createElement("p");
  state.className = "stage-indicator";
  card.append(state);
  const details = document.createElement("dl");
  card.append(details);
  const actions = document.createElement("div");
  actions.className = "stage-actions";
  addLink(actions, "Open Stage Console", `/stage?stage=${encodeURIComponent(stage.stage_id)}`);
  addLink(actions, "Open Audience View", `/audience/${encodeURIComponent(stage.stage_id)}`);
  const overlayUrl = `/overlay/${encodeURIComponent(stage.stage_id)}?lang=original`;
  addLink(actions, "Open Original Overlay", overlayUrl);
  addLink(actions, "Open Spanish Overlay", `/overlay/${encodeURIComponent(stage.stage_id)}?lang=es`);
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy overlay URL";
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(new URL(overlayUrl, location.href).href);
      copy.textContent = "Copied";
    } catch (error) {
      copy.textContent = `Copy failed: ${error.message}`;
    }
  };
  actions.append(copy);
  card.append(actions);
  container.append(card);
  cards.set(stage.stage_id, { card, heading, state, details });
  return cards.get(stage.stage_id);
}

function renderStage(stage) {
  const entry = cards.get(stage.stage_id) || createCard(stage);
  entry.heading.textContent = `${stage.name} (${stage.stage_id})`;
  const label = stateLabel(stage);
  entry.state.textContent = label;
  entry.state.dataset.state = label;
  const values = [
    ["Stage", stage.state],
    ["Browser connected", String(stage.browser_connected)],
    ["Audio receiving", `${stage.audio_receiving} · last audio ${since(stage.last_audio_at)}`],
    ["Provider connected", `${stage.provider_connected} · ${stage.provider_status}`],
    ["Connections", stage.connection_count],
    ["Reconnects", stage.reconnect_count],
    ["Session age", connectionAge(stage.session_age_seconds)],
    ["Connection age", connectionAge(stage.connection_age_seconds)],
    ["Last caption", since(stage.last_caption_at)],
    ["Last error", stage.last_error || stage.error || "—"],
    ["Subscribers", stage.viewers],
  ];
  entry.details.replaceChildren();
  for (const [name, value] of values) {
    const term = document.createElement("dt");
    term.textContent = name;
    const description = document.createElement("dd");
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
    controlStatus.textContent = `${stages.length} configured stage${stages.length === 1 ? "" : "s"} · updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    controlStatus.textContent = `Stage status unavailable: ${error.message}`;
  }
}

refresh();
setInterval(refresh, 1000);
