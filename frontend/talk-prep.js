import { t } from "/static/i18n.js";

const title = document.querySelector("#talk-title");
const speaker = document.querySelector("#talk-speaker");
const abstract = document.querySelector("#talk-abstract");
const suggestButton = document.querySelector("#suggest-terms");
const addButton = document.querySelector("#add-term");
const applyButton = document.querySelector("#apply-terms");
const list = document.querySelector("#talk-terms");
const status = document.querySelector("#talk-prep-status");

let stageId = "";
let rows = [];
let activeCount = 0;
let locked = true;
let loaded = false;
let busy = false;
let message = { key: "prepRulesActive", values: { count: 0 } };

function show(key, values = {}) {
  message = { key, values };
  status.textContent = t(key, values);
}

function sync() {
  const disabled = !stageId || !loaded || locked || busy;
  for (const input of [title, speaker, abstract, suggestButton]) input.disabled = disabled;
  applyButton.disabled = disabled || rows.length === 0;
  addButton.disabled = disabled || rows.length >= 15;
  for (const input of list.querySelectorAll("input, button")) input.disabled = disabled;
}

function renderRows() {
  list.replaceChildren();
  for (const row of rows) {
    const wrapper = document.createElement("div");
    wrapper.className = "talk-term";
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = row.enabled;
    enabled.dataset.i18nAriaLabel = "prepEnabled";
    enabled.setAttribute("aria-label", t("prepEnabled"));
    enabled.onchange = () => { row.enabled = enabled.checked; };
    const canonical = document.createElement("input");
    canonical.type = "text";
    canonical.className = "term-canonical";
    canonical.maxLength = 80;
    canonical.value = row.canonical;
    canonical.placeholder = t("prepTerm");
    canonical.dataset.i18nAriaLabel = "prepTerm";
    canonical.setAttribute("aria-label", t("prepTerm"));
    canonical.oninput = () => { row.canonical = canonical.value; };
    const variants = document.createElement("input");
    variants.type = "text";
    variants.className = "term-variants";
    variants.value = row.variants.join(", ");
    variants.placeholder = t("prepVariants");
    variants.dataset.i18nAriaLabel = "prepVariants";
    variants.setAttribute("aria-label", t("prepVariants"));
    variants.oninput = () => { row.variants = variants.value.split(",").map((item) => item.trim()).filter(Boolean); };
    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.i18n = "removeTerm";
    remove.textContent = t("removeTerm");
    remove.onclick = () => {
      rows = rows.filter((item) => item !== row);
      renderRows();
    };
    wrapper.append(enabled, canonical, variants, remove);
    list.append(wrapper);
  }
  sync();
}

async function loadStage(nextStageId) {
  stageId = nextStageId;
  rows = [];
  activeCount = 0;
  locked = true;
  loaded = false;
  busy = false;
  title.value = "";
  speaker.value = "";
  abstract.value = "";
  show("prepRulesActive", { count: 0 });
  renderRows();
  if (!stageId) return;
  const requested = stageId;
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(requested)}/talk-prep`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const state = await response.json();
    if (requested !== stageId) return;
    activeCount = state.active_count;
    locked = !state.editable;
    loaded = true;
    renderRows();
    show(locked ? "prepLocked" : "prepRulesActive", { count: activeCount });
  } catch (_error) {
    if (requested === stageId) show("prepLoadFailed");
  }
  sync();
}

function setActive(active) {
  if (locked === active) return;
  locked = active;
  show(active ? "prepLocked" : "prepRulesActive", { count: activeCount });
  sync();
}

suggestButton.onclick = async () => {
  if (!stageId || locked || busy) return;
  const metadata = { title: title.value.trim(), speaker: speaker.value.trim(), abstract: abstract.value.trim() };
  if (!metadata.title || !metadata.abstract) {
    show("prepMetadataRequired");
    return;
  }
  const requested = stageId;
  rows = [];
  renderRows();
  busy = true;
  sync();
  show("prepSuggesting");
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(requested)}/talk-prep/suggestions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(metadata),
    });
    if (requested !== stageId) return;
    if (!response.ok) {
      show(response.status === 409 ? "prepLocked" : response.status === 422
        ? "prepMetadataRequired" : "prepSuggestionFailed", { status: response.status });
      return;
    }
    const suggested = (await response.json()).terms;
    if (requested !== stageId) return;
    rows = suggested.map((term) => ({ enabled: true, ...term }));
    renderRows();
    show(rows.length ? "prepSuggestionsReady" : "prepNoSuggestions", { count: rows.length });
  } catch (_error) {
    if (requested === stageId) show("prepSuggestionFailed");
  } finally {
    if (requested === stageId) { busy = false; sync(); }
  }
};

addButton.onclick = () => {
  if (!stageId || locked || busy || rows.length >= 15) return;
  rows.push({ enabled: true, canonical: "", variants: [] });
  renderRows();
  list.lastElementChild.querySelector(".term-canonical").focus();
};

applyButton.onclick = async () => {
  if (!stageId || locked || busy) return;
  const terms = rows.filter((row) => row.enabled).map((row) => ({
    canonical: row.canonical.trim(), variants: row.variants,
  }));
  if (terms.some((term) => !term.canonical)) {
    show("prepInvalidTerms");
    return;
  }
  const requested = stageId;
  busy = true;
  sync();
  try {
    const response = await fetch(`/api/stages/${encodeURIComponent(requested)}/talk-prep`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ terms }),
    });
    if (requested !== stageId) return;
    if (!response.ok) {
      show(response.status === 409 ? "prepLocked" : response.status === 422
        ? "prepInvalidTerms" : "prepApplyFailed", { status: response.status });
      return;
    }
    const state = await response.json();
    if (requested !== stageId) return;
    activeCount = state.active_count;
    rows = [];
    renderRows();
    show("prepRulesActive", { count: activeCount });
  } catch (_error) {
    if (requested === stageId) show("prepApplyFailed");
  } finally {
    if (requested === stageId) { busy = false; sync(); }
  }
};

export const talkPrep = {
  loadStage,
  setActive,
  refreshLanguage() {
    show(message.key, message.values);
    for (const input of list.querySelectorAll(".term-canonical")) input.placeholder = t("prepTerm");
    for (const input of list.querySelectorAll(".term-variants")) input.placeholder = t("prepVariants");
  },
};
