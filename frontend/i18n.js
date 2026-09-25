import en from "/static/locales/en.js";
import es from "/static/locales/es.js";

const catalog = { en, es };
const storageKey = "stagepulse-ui-language";
let language = localStorage.getItem(storageKey) === "es" ? "es" : "en";

export function t(key, values = {}) {
  const template = catalog[language][key] ?? catalog.en[key] ?? key;
  return template.replace(/{{(\w+)}}/g, (_match, name) => String(values[name] ?? ""));
}

export function stateText(value) {
  return t(`state_${value}`);
}

export function initI18n(onChange = () => {}) {
  const selector = document.querySelector("#ui-language");
  function render() {
    document.documentElement.lang = language;
    if (selector) selector.value = language;
    for (const element of document.querySelectorAll("[data-i18n]")) {
      element.textContent = t(element.dataset.i18n);
    }
    for (const element of document.querySelectorAll("[data-i18n-alt]")) {
      element.alt = t(element.dataset.i18nAlt);
    }
    onChange();
  }
  if (selector) selector.onchange = () => {
    language = selector.value === "es" ? "es" : "en";
    localStorage.setItem(storageKey, language);
    render();
  };
  window.addEventListener("storage", (event) => {
    if (event.key === storageKey) {
      language = event.newValue === "es" ? "es" : "en";
      render();
    }
  });
  render();
}
