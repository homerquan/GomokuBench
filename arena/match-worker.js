const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
// Wllama 4 selects the compatible WASM build on Safari, including when JSPI is available.
const WLLAMA = "https://cdn.jsdelivr.net/npm/@wllama/wllama@4.0.0/esm/";
const models = new Map();

function progress(message, percent) {
  self.postMessage({ type: "progress", message, percent });
}

async function loadEngine() {
  progress("Starting the GomokuBench engine…");
  const { loadPyodide } = await import(PYODIDE + "pyodide.mjs");
  const python = await loadPyodide({ indexURL: PYODIDE });
  const sources = ["core.py", "libs/__init__.py", "libs/piece.py", "libs/board.py",
    "libs/ai.py", "libs/eval_fn.py", "libs/llm_player.py", "rule.md"];
  python.FS.mkdir("libs");
  await Promise.all([
    python.loadPackage("numpy"),
    ...sources.map(async (name) => {
      const response = await fetch(new URL(name, import.meta.url));
      if (!response.ok) throw new Error("The benchmark engine could not be loaded.");
      python.FS.writeFile(name, new Uint8Array(await response.arrayBuffer()));
    }),
  ]);
  const call = python.runPython("from core import browser_command\nbrowser_command");
  return (action, fields = {}) => JSON.parse(call(JSON.stringify({ action, ...fields })));
}

async function canCacheModels() {
  try {
    const storage = await navigator.storage?.getDirectory();
    return typeof storage?.entries === "function";
  } catch {
    return false;
  }
}

function temporaryModelStorage() {
  const files = new Map();
  return {
    isSupported: () => true,
    read: async (key) => files.get(key) ?? null,
    write: async (key, stream) => { files.set(key, await new Response(stream).blob()); },
    getSize: async (key) => files.get(key)?.size ?? -1,
    list: async () => Array.from(files, ([key, blob]) => ({ key, size: blob.size })),
    delete: async (key) => { files.delete(key); },
  };
}

async function loadModel(player) {
  if (models.has(player.id)) return models.get(player.id);
  progress(`Loading ${player.name} · ${player.mb} MB on first use…`, 0);
  const { CacheManager, Wllama } = await import(WLLAMA + "index.js");
  const url = `https://huggingface.co/${player.repo}/resolve/${player.revision}/${encodeURIComponent(player.file)}`;
  const options = {
    n_ctx: 2048,
    n_threads: Math.max(1, Math.min(4, (navigator.hardwareConcurrency || 2) / 2 | 0)),
    n_gpu_layers: 0,
    progressCallback({ loaded, total }) {
      const percent = total > 0 ? Math.min(100, Math.round(100 * loaded / total)) : undefined;
      progress(`Loading ${player.name}${percent === undefined ? "…" : ` · ${percent}%`}`, percent);
    },
  };
  function createModel(persistent) {
    const cacheManager = persistent ? undefined : new CacheManager([temporaryModelStorage()]);
    return new Wllama({ default: WLLAMA + "wasm/wllama.wasm" }, {
      cacheManager, suppressNativeLog: true, allowOffline: true,
    });
  }
  const persistent = await canCacheModels();
  let model = createModel(persistent);
  try {
    try {
      await model.loadModelFromUrl(url, options);
    } catch (error) {
      if (!persistent || !(error instanceof DOMException)) throw error;
      model = createModel(false);
      await model.loadModelFromUrl(url, options);
    }
  } catch (error) {
    const status = String(error?.message ?? error).match(/\bHTTP (\d{3})\b/)?.[1];
    if (status === "429") {
      throw new Error("Hugging Face rate-limited the model download. Try again in a few minutes.");
    }
    if (status) throw new Error(`Hugging Face returned HTTP ${status} while downloading ${player.name}.`);
    throw new Error(`Could not load ${player.name}. Check your connection or try a smaller model.`);
  }
  models.set(player.id, model);
  return model;
}

async function endpointMove(player, request) {
  const headers = { "Content-Type": "application/json" };
  if (player.token) headers.Authorization = "Bearer " + player.token;
  let response;
  try {
    response = await fetch(player.endpoint, {
      method: "POST", headers, credentials: "omit", redirect: "error",
      cache: "no-store", signal: AbortSignal.timeout(60_000),
      body: JSON.stringify({
        model: player.model,
        messages: [{ role: "system", content: request.system }, { role: "user", content: request.prompt }],
        max_tokens: 1024, stream: false,
      }),
    });
  } catch (error) {
    if (error.name === "TimeoutError") throw new Error("The endpoint took more than 60 seconds to reply.");
    throw new Error("Could not reach the endpoint. Check its HTTPS URL and allow this Space’s origin in CORS.");
  }
  if (!response.ok) throw new Error(`The endpoint returned HTTP ${response.status}. Check its token, model ID, and quota.`);
  const payload = await response.json().catch(() => null);
  const message = payload?.choices?.[0]?.message;
  if (!message) throw new Error("The endpoint must return OpenAI-compatible chat completions JSON.");
  const content = Array.isArray(message.content)
    ? message.content.map((part) => part?.text || "").join("")
    : message.content;
  return String(content || message.reasoning_content || "");
}

async function play(players) {
  const run = await loadEngine();
  let state = run("start");
  self.postMessage({ type: "state", state });
  while (state.next) {
    const player = players[state.next];
    const color = state.next === "black" ? "Black" : "White";
    let move;
    if (player.kind === "public") {
      const model = await loadModel(player);
      progress(`${color} · ${player.name} is choosing a move…`);
      const request = run("request", { template: player.template || "chatml" });
      const answer = await model.createCompletion({
        ...request, max_tokens: 12, temperature: 0,
      });
      move = answer.choices[0]?.text || "";
    } else {
      progress(`${color} · ${player.name} is choosing a move…`);
      move = player.kind === "oracle" ? run("oracle") : await endpointMove(player, run("request"));
    }
    let next = run("move", { move });
    if (next.invalid && player.kind === "custom") {
      progress(`${color} returned an illegal move. Retrying…`);
      next = run("move", { move: await endpointMove(player, run("request", { retry: true })) });
    }
    if (next.invalid) {
      throw new Error(`${color} returned an invalid move. The match ended without a result.`);
    }
    state = next;
    self.postMessage({ type: "state", state });
    await new Promise((resolve) => setTimeout(resolve, 45));
  }
}

self.onmessage = async ({ data }) => {
  try {
    await play(data);
  } catch (error) {
    self.postMessage({
      type: "error",
      message: error instanceof Error ? error.message : "Could not complete this match.",
    });
  }
};
