import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("venue display reconnects once and replaces stale captions with snapshot", async () => {
  const listeners = new Map();
  const elements = new Map();
  function element() {
    return {
      textContent: "", dataset: {}, hidden: false,
      classes: new Set(),
      classList: { toggle(name, enabled) { this.owner.classes[enabled ? "add" : "delete"](name); } },
    };
  }
  for (const selector of ["#display-status", "#display-original .display-text",
    "#display-spanish .display-text", "#display-original", "#display-spanish",
    "#display-captions", "#display-stage"]) {
    const item = element();
    item.classList.owner = item;
    elements.set(selector, item);
  }
  const document = {
    visibilityState: "visible",
    querySelector: (selector) => elements.get(selector),
    addEventListener: (name, callback) => listeners.set(`document:${name}`, callback),
  };
  const window = {
    addEventListener: (name, callback) => listeners.set(`window:${name}`, callback),
  };
  let timerId = 0;
  const timers = new Map();
  class WebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    static instances = [];
    constructor(url) {
      this.url = url;
      this.readyState = WebSocket.CONNECTING;
      WebSocket.instances.push(this);
    }
  }
  const originals = new Map();
  const globals = {
    document, window, WebSocket,
    location: { pathname: "/display/main", search: "?lang=both", protocol: "https:", host: "example.com" },
    fetch: async () => ({ ok: true, json: async () => ({ name: "Main Stage" }) }),
    setTimeout: (callback) => { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
    __testI18n: { initI18n: (callback) => callback(), t: (key) => key },
  };
  for (const [key, value] of Object.entries(globals)) {
    originals.set(key, globalThis[key]);
    globalThis[key] = value;
  }
  try {
    const source = await readFile(new URL("../frontend/display.js", import.meta.url), "utf8");
    const runnable = source.replace(
      'import { initI18n, t } from "/static/i18n.js";',
      "const { initI18n, t } = globalThis.__testI18n;",
    );
    await import(`data:text/javascript,${encodeURIComponent(runnable)}`);
    assert.equal(WebSocket.instances.length, 1);
    const first = WebSocket.instances[0];
    assert.equal(first.url, "wss://example.com/ws/stages/main/captions");
    first.readyState = WebSocket.OPEN;
    first.onopen();
    first.onmessage({ data: JSON.stringify({ language: "en", text: "Current English", is_final: true }) });
    first.onmessage({ data: JSON.stringify({ language: "es", text: "Español actual", is_final: false }) });
    assert.equal(elements.get("#display-original .display-text").textContent, "Current English");
    assert.equal(elements.get("#display-spanish .display-text").textContent, "Español actual");
    assert.equal(elements.get("#display-captions").dataset.mode, "both");
    first.readyState = WebSocket.CLOSED;
    first.onclose();
    assert.equal(elements.get("#display-original .display-text").textContent, "captionsReconnecting");
    assert.equal(timers.size, 1);
    listeners.get("document:visibilitychange")();
    listeners.get("window:pageshow")();
    assert.equal(WebSocket.instances.length, 2);
    assert.equal(timers.size, 0);
    const second = WebSocket.instances[1];
    second.readyState = WebSocket.OPEN;
    second.onopen();
    second.onmessage({ data: JSON.stringify({ language: "en", text: "Snapshot EN", is_final: true }) });
    second.onmessage({ data: JSON.stringify({ language: "es", text: "Snapshot ES", is_final: true }) });
    first.onmessage({ data: JSON.stringify({ language: "en", text: "Stale", is_final: true }) });
    assert.equal(elements.get("#display-original .display-text").textContent, "Snapshot EN");
    assert.equal(elements.get("#display-spanish .display-text").textContent, "Snapshot ES");
    listeners.get("window:online")();
    assert.equal(WebSocket.instances.length, 2);
  } finally {
    for (const [key, value] of originals) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});
