import { initI18n, t } from "/static/i18n.js";

const container = document.querySelector("#hub-stages");
const status = document.querySelector("#hub-status");

function statusKey(stage) {
  if (stage.state === "failed" || stage.translation_stall_active
      || (stage.state === "running" && (!stage.provider_connected || !stage.audio_receiving))) return "hubAttention";
  if (stage.state === "running" && stage.provider_connected) return "hubLive";
  if (["stopped", "completed"].includes(stage.state)) return "hubStopped";
  return "hubWaiting";
}

async function refresh() {
  try {
    const response = await fetch("/api/stages", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const stages = await response.json();
    container.replaceChildren();
    for (const stage of stages) {
      const link = document.createElement("a");
      link.className = "hub-stage";
      link.href = `/audience/${encodeURIComponent(stage.stage_id)}`;
      const title = document.createElement("h2");
      title.textContent = stage.name;
      const id = document.createElement("small");
      id.textContent = stage.stage_id;
      const badge = document.createElement("span");
      const key = statusKey(stage);
      badge.className = "stage-indicator";
      badge.dataset.state = key === "hubLive" ? "connected" : key === "hubAttention" ? "attention" : "waiting";
      badge.textContent = t(key);
      const label = document.createElement("span");
      label.className = "hub-stage-label";
      label.textContent = t("hubOpenStage");
      link.append(title, id, badge, label);
      container.append(link);
    }
    status.textContent = t("hubStageCount", { count: stages.length });
  } catch (error) {
    status.textContent = t("cannotLoadStages", { error: error.message });
  }
}

initI18n(refresh);
setInterval(refresh, 5000);
