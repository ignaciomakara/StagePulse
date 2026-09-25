import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("audience reconnects once on return and keeps the caption language", async () => {
  const listeners = new Map();
  let rerenderUi;
  const elements = new Map();
  const element = () => ({
    textContent: "",
    value: "",
    dataset: {},
    children: [],
    replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); },
  });
  for (const id of ["connection", "captions", "language", "fullscreen", "stage-title"]) {
    elements.set(`#${id}`, element());
  }
  elements.get("#language").value = "es";
  const document = {
    visibilityState: "visible",
    querySelector: (selector) => elements.get(selector),
    createElement: element,
    addEventListener: (name, callback) => listeners.set(`document:${name}`, callback),
  };
  const window = {
    addEventListener: (name, callback) => listeners.set(`window:${name}`, callback),
  };
  const timers = new Map();
  let nextTimer = 0;
  class WebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
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
    location: { pathname: "/audience/main", protocol: "https:", host: "example.com" },
    fetch: async () => ({ ok: true, json: async () => ({ name: "Main" }) }),
    setTimeout: (callback) => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
    __testI18n: { initI18n: (callback) => { rerenderUi = callback; callback(); }, t: (key) => key },
  };
  for (const [key, value] of Object.entries(globals)) {
    originals.set(key, globalThis[key]);
    globalThis[key] = value;
  }
  try {
    const source = await readFile(new URL("../frontend/audience.js", import.meta.url), "utf8");
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
    first.onmessage({ data: JSON.stringify({ language: "es", text: "Anterior", is_final: true }) });
    assert.equal(elements.get("#captions").children[0].textContent, "Anterior");
    for (const text of ["Primera", "Segunda", "Tercera", "Cuarta"]) {
      first.onmessage({ data: JSON.stringify({ language: "es", text, is_final: true }) });
    }
    assert.deepEqual(elements.get("#captions").children.map((line) => line.textContent),
      ["Segunda", "Tercera", "Cuarta"]);
    assert.deepEqual(elements.get("#captions").children.map((line) => line.className),
      ["previous", "previous", "current"]);
    first.onmessage({ data: JSON.stringify({ language: "es", text: "Adelanto", is_final: false }) });
    first.onmessage({ data: JSON.stringify({ language: "es", text: "Adelanto actualizado", is_final: false }) });
    assert.deepEqual(elements.get("#captions").children.map((line) => line.textContent),
      ["Tercera", "Cuarta", "Adelanto actualizado"]);
    first.onmessage({ data: JSON.stringify({ language: "es", text: "Frase final", is_final: true }) });
    assert.deepEqual(elements.get("#captions").children.map((line) => line.textContent),
      ["Tercera", "Cuarta", "Frase final"]);
    first.onmessage({ data: JSON.stringify({ language: "en", text: "English current", is_final: true }) });
    elements.get("#language").value = "en";
    elements.get("#language").onchange();
    assert.deepEqual(elements.get("#captions").children.map((line) => line.textContent), ["English current"]);
    rerenderUi();
    assert.deepEqual(elements.get("#captions").children.map((line) => line.textContent), ["English current"]);
    elements.get("#language").value = "es";
    elements.get("#language").onchange();
    assert.deepEqual(elements.get("#captions").children.map((line) => line.textContent),
      ["Tercera", "Cuarta", "Frase final"]);

    document.visibilityState = "hidden";
    listeners.get("document:visibilitychange")();
    assert.equal(WebSocket.instances.length, 1);
    first.readyState = WebSocket.CLOSED;
    first.onclose();
    assert.equal(elements.get("#connection").textContent, "captionsReconnecting");
    assert.equal(elements.get("#captions").children[0].textContent, "captionsReconnecting");
    assert.equal(timers.size, 1);

    document.visibilityState = "visible";
    listeners.get("document:visibilitychange")();
    listeners.get("window:pageshow")();
    listeners.get("window:online")();
    assert.equal(WebSocket.instances.length, 2);
    assert.equal(timers.size, 0);
    assert.equal(elements.get("#language").value, "es");
    const second = WebSocket.instances[1];
    second.readyState = WebSocket.OPEN;
    second.onopen();
    second.onmessage({ data: JSON.stringify({ language: "es", text: "Actual", is_final: true }) });
    assert.deepEqual(elements.get("#captions").children.map((line) => line.textContent), ["Actual"]);
    first.onclose();
    listeners.get("window:pageshow")();
    assert.equal(WebSocket.instances.length, 2);
    assert.equal(timers.size, 0);
    assert.equal(elements.get("#connection").textContent, "captionsConnected");
  } finally {
    for (const [key, value] of originals) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});
