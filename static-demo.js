// Static showcase: runs the dashboard's own Python server inside the browser
// (Pyodide), so this site behaves like the real app with simulated machines.
// It provides window.ergBackend, which the dashboard and session pages use
// instead of HTTP and WebSocket when it is present.
(() => {
  // The session page opens in a drawer (iframe) and shares the dashboard's server.
  if (window.parent !== window && window.parent.ergBackend) {
    window.ergBackend = window.parent.ergBackend;
    return;
  }

  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
  const listeners = new Set();
  let app = null;
  let last = null;

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = src;
      el.onload = resolve;
      el.onerror = () => reject(new Error(`Could not load ${src}`));
      document.head.append(el);
    });
  }

  async function text(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`Could not load ${path}`);
    return res.text();
  }

  function tick() {
    last = JSON.parse(app.step());
    listeners.forEach((cb) => cb(last));
  }

  const ready = (async () => {
    await loadScript(`${PYODIDE}pyodide.js`);
    const py = await loadPyodide({ indexURL: PYODIDE });
    await py.loadPackage("pyyaml");
    py.FS.mkdirTree("/app/erg");
    const modules = JSON.parse(await text("py/modules.json"));
    for (const name of modules) {
      py.FS.writeFile(`/app/erg/${name}.py`, await text(`py/erg/${name}.py`));
    }
    py.FS.writeFile("/app/showcase_app.py", await text("py/showcase_app.py"));
    py.FS.writeFile("/app/config.yaml", await text("config.yaml"));
    py.runPython("import sys; sys.path.insert(0, '/app')");
    app = py.pyimport("showcase_app").Showcase("/app/config.yaml");
    tick();
    setInterval(tick, 1000);
  })();

  window.ergBackend = {
    async fetch(path, options = {}) {
      await ready;
      const res = JSON.parse(app.request(options.method || "GET", path, options.body || ""));
      if (options.method === "POST") tick(); // show the effect without waiting a second
      return { ok: res.status < 400, status: res.status, json: async () => res.body };
    },
    subscribe(cb) {
      listeners.add(cb);
      if (last) cb(last);
    },
  };

  // --- showcase chrome: loading screen and the "Start a session" drawer ------

  const style = document.createElement("style");
  style.textContent = `
    #sc-loading { position: fixed; inset: 0; z-index: 50; background: #000; color: #fff;
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px;
      font-family: "Racing Sans One", "Arial Black", sans-serif; font-size: 32px; text-align: center; padding: 16px; }
    #sc-loading small { font-family: Helvetica, Arial, sans-serif; font-size: 16px; color: #9a99a0; font-style: oblique; }
    #sc-drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(480px, 100vw); z-index: 30;
      background: #000; border-left: 3px solid #e30613; transform: translateX(100%); transition: transform .3s; }
    #sc-drawer.open { transform: none; }
    #sc-drawer iframe { width: 100%; height: 100%; border: 0; }
    #sc-drawer .close { position: absolute; top: 8px; right: 12px; z-index: 1; background: none; border: 0;
      color: #fff; font-size: 32px; cursor: pointer; }
    #controls .sc-session { background: #e30613; }
  `;
  document.head.append(style);

  document.addEventListener("DOMContentLoaded", () => {
    const loading = document.createElement("div");
    loading.id = "sc-loading";
    loading.innerHTML = "Chargement de la démo…<small>La simulation démarre dans votre navigateur. Quelques secondes la première fois.</small>";
    document.body.append(loading);
    ready.then(
      () => loading.remove(),
      (err) => { loading.innerHTML = `La démo n’a pas pu démarrer.<small>${err.message}</small>`; },
    );

    const drawer = document.createElement("div");
    drawer.id = "sc-drawer";
    drawer.innerHTML = `<button class="close" aria-label="Fermer">×</button><iframe title="Lancer une séance"></iframe>`;
    document.body.append(drawer);
    const close = () => drawer.classList.remove("open");
    drawer.querySelector(".close").addEventListener("click", close);

    const button = document.createElement("button");
    button.className = "sc-session";
    button.textContent = "★ Lancer une séance";
    button.addEventListener("click", () => {
      drawer.querySelector("iframe").src = "session.html"; // fresh form each time
      drawer.classList.add("open");
    });
    document.getElementById("controls")?.append(button);

    // Once a session starts, get the drawer out of the way so the screen is visible.
    listeners.add((state) => { if (state.workout) close(); });
  });
})();
