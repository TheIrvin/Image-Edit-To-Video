"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = {
  project: null,
  selected: 0,
  voices: [],
  status: null,
  jobs: [],
  exports: [],
  page: "studio",
  dirty: false,
  serial: 0,
  saving: null,
  saveTimer: null,
  times: {},
  tracked: new Set(),
  dock: null,
  historyFilter: "exports",
  videoMode: false,
};
const motions = {
  auto: "Automático · variar",
  zoom_in: "Acercamiento lento",
  zoom_out: "Alejamiento lento",
  pan_right: "Izquierda → derecha",
  pan_left: "Derecha → izquierda",
  pan_down: "Arriba → abajo",
  pan_up: "Abajo → arriba",
  diagonal_in: "Diagonal + acercamiento",
  diagonal_out: "Diagonal + alejamiento",
  still: "Imagen fija",
};
const transitions = {
  auto: "Automática · variar",
  cut: "Corte directo",
  fade: "Fundido cruzado",
  fadeblack: "Fundido por negro",
  wipeleft: "Cortinilla hacia la izquierda",
  wiperight: "Cortinilla hacia la derecha",
  slideleft: "Deslizamiento izquierdo",
  slideright: "Deslizamiento derecho",
  circleopen: "Apertura circular",
};
const filters = {
  none: "Original",
  warm: "Cálido",
  cool: "Frío",
  mono: "Blanco y negro",
  sepia: "Sepia",
  cinema: "Cine suave",
};
function element(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, String(value));
  }
  if (text) node.textContent = text;
  return node;
}
function options(select, choices) {
  select.replaceChildren(
    ...Object.entries(choices).map(([value, label]) =>
      element("option", { value }, label),
    ),
  );
}
async function api(path, method = "GET", body) {
  const response = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch {
      data = { detail: "El servidor no pudo completar la operación." };
    }
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  }
  return response.json();
}
let toastTimer;
function toast(text, error = false) {
  clearTimeout(toastTimer);
  $("#toast").textContent = text;
  $("#toast").className = error ? "error" : "";
  $("#toast").hidden = false;
  toastTimer = setTimeout(
    () => ($("#toast").hidden = true),
    error ? 8500 : 3800,
  );
}
function report(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (error) {
      toast(error.message, true);
    }
  };
}
function seconds(value) {
  return value < 60
    ? `${value.toFixed(1).replace(".", ",")} s`
    : `${Math.floor(value / 60)}:${String(Math.round(value % 60)).padStart(2, "0")}`;
}
function date(value) {
  return new Intl.DateTimeFormat("es", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value * 1000));
}
function selectedScene() {
  return state.project?.scenes[state.selected];
}
function imageUrl(scene, aspect = "original") {
  const revision =
    aspect === "original" ? "" : `&revision=${state.project.revision}`;
  return `/api/projects/${state.project.id}/scenes/${scene.number}/image?aspect=${encodeURIComponent(aspect)}${revision}`;
}
function setPage(page) {
  state.page = page;
  for (const item of ["studio", "voices", "history"])
    $(`#${item}-page`).hidden = item !== page;
  $$("[data-page]").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === page),
  );
  $("#page-title").textContent = {
    studio: "Estudio",
    voices: "Biblioteca de voces",
    history: "Videos e historial",
  }[page];
  if (page === "voices")
    report(async () => {
      await Promise.all([refreshStatus(), refreshVoices()]);
    })();
  if (page === "history") report(refreshHistory)();
}
async function refreshProjects() {
  const items = await api("/api/projects");
  $("#project-list").replaceChildren(
    ...items.map((p) => {
      const button = element("button", {
        class: `project-link ${state.project?.id === p.id ? "active" : ""}`,
        onclick: report(() => openProject(p.id)),
      });
      button.append(element("span", {}, "◻"), element("span", {}, p.name));
      button.title = `${p.name} · ${p.scenes} escenas`;
      return button;
    }),
  );
}
async function openProject(id) {
  if (state.project && state.dirty) await saveProject();
  state.project = await api(`/api/projects/${id}`);
  state.selected = 0;
  state.times = {};
  const previous = state.exports.find(
    (item) =>
      item.project_id === id &&
      item.project_revision === state.project.revision,
  );
  for (const timing of previous?.timing || [])
    state.times[timing.number] = timing;
  state.dirty = false;
  state.videoMode = false;
  state.serial = 0;
  setPage("studio");
  renderEditor();
  await refreshProjects();
}
function renderEditor() {
  const p = state.project;
  $("#welcome").hidden = !!p;
  $("#editor").hidden = !p;
  if (!p) return;
  $("#project-name").value = p.name;
  $("#scene-count").textContent = p.scenes.length;
  $("#save-state").textContent = "Guardado";
  $("#project-summary").textContent =
    `${p.scenes.length} escenas · ${p.script_mode === "paragraphs" ? "TXT por párrafos" : "TXT con etiquetas"} · ${p.pairing === "number" ? "números del archivo" : "orden natural de las imágenes"}`;
  $("#project-warnings").textContent = p.warnings.join(" ");
  $("#project-warnings").hidden = !p.warnings.length;
  renderSceneList();
  renderVoiceSelect();
  for (const [selector, key] of Object.entries({
    "#resolution": "resolution",
    "#global-fit": "fit",
    "#fps": "fps",
    "#pause": "pause",
    "#transition-duration": "transition_duration",
    "#speed": "speed",
  }))
    $(selector).value = p.settings[key];
  $("#subtitles").checked = p.settings.subtitles;
  renderSceneControls();
  renderCanvas();
  updateDimensions();
}
function renderSceneList() {
  if (!state.project) return;
  const query = $("#scene-search").value.trim().toLowerCase();
  const list = [];
  state.project.scenes.forEach((scene, index) => {
    if (
      query &&
      !`${scene.number} ${scene.source_name} ${scene.text}`
        .toLowerCase()
        .includes(query)
    )
      return;
    const button = element("button", {
      class: `scene-item ${index === state.selected ? "active" : ""}`,
      onclick: () => {
        state.selected = index;
        state.videoMode = false;
        renderSceneControls();
        renderCanvas();
        renderSceneList();
      },
    });
    button.append(
      element("img", {
        class: "scene-thumb",
        src: imageUrl(scene),
        alt: "",
        loading: "lazy",
      }),
    );
    const details = element("div", { class: "scene-description" });
    details.append(
      element("strong", {}, `img${scene.number} · ${scene.source_name}`),
      element("p", {}, scene.text),
      element(
        "small",
        {},
        state.times[scene.number]
          ? `${seconds(state.times[scene.number].audio_duration + state.project.settings.pause + scene.extra_pause)} · medido`
          : "Duración por medir",
      ),
    );
    button.append(details);
    list.push(button);
  });
  $("#scene-list").replaceChildren(...list);
}
function renderSceneControls() {
  const scene = selectedScene();
  if (!scene) return;
  $("#selected-scene-label").textContent = `Escena ${scene.number}`;
  $("#selected-duration").textContent = state.times[scene.number]
    ? `Voz: ${seconds(state.times[scene.number].audio_duration)}`
    : "Duración basada en el audio";
  $("#scene-text").value = scene.text;
  $("#scene-motion").value = scene.motion;
  $("#scene-transition").value = scene.transition;
  $("#scene-filter").value = scene.filter;
  $("#scene-fit").value = scene.fit;
  $("#extra-pause").value = scene.extra_pause;
  $("#focus-x").value = scene.focus_x;
  $("#focus-y").value = scene.focus_y;
  updateFocusLabels();
}
function updateFocusLabels() {
  $("#focus-x-value").value =
    `${Math.round(Number($("#focus-x").value) * 100)}%`;
  $("#focus-y-value").value =
    `${Math.round(Number($("#focus-y").value) * 100)}%`;
}
function renderCanvas() {
  if (!state.project) return;
  const scene = selectedScene();
  const aspect = state.project.settings.aspect;
  $$("[data-aspect]").forEach((b) =>
    b.classList.toggle("selected", b.dataset.aspect === aspect),
  );
  $("#canvas-placeholder").hidden = true;
  $("#image-canvas").hidden = state.videoMode;
  $("#video-player").hidden = !state.videoMode;
  $("#back-to-image").hidden = !state.videoMode;
  $("#image-canvas").classList.toggle("vertical", aspect === "9:16");
  $("#video-player").classList.toggle("vertical", aspect === "9:16");
  if (!state.videoMode) {
    $("#video-player").pause();
    $("#scene-image").src = imageUrl(scene, aspect);
    $("#canvas-title").textContent = "Vista del encuadre";
    $("#canvas-caption").textContent =
      `${scene.source_name} · ${aspect} · imagen sin audio`;
  }
  updateDimensions();
}
function updateDimensions() {
  if (!state.project) return;
  const sizes = {
    720: [1280, 720],
    1080: [1920, 1080],
    1260: [2240, 1260],
    "2k": [2560, 1440],
  };
  let [w, h] = sizes[state.project.settings.resolution];
  if (state.project.settings.aspect === "9:16") [w, h] = [h, w];
  $("#output-dimensions").textContent =
    `${w} × ${h} px · ${state.project.settings.aspect}`;
}
function markDirty({ audio = false } = {}) {
  if (!state.project) return;
  state.dirty = true;
  state.serial++;
  $("#save-state").textContent = "Cambios pendientes";
  if (audio) {
    state.times = {};
    $("#selected-duration").textContent = "Duración por medir";
    renderSceneList();
  }
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(
    () => saveProject().catch((e) => toast(`No se guardó: ${e.message}`, true)),
    700,
  );
}
async function saveProject() {
  clearTimeout(state.saveTimer);
  if (state.saving) {
    await state.saving;
    if (state.dirty) return saveProject();
    return state.project;
  }
  if (!state.project || !state.dirty) return state.project;
  const p = state.project;
  const serial = state.serial;
  const payload = {
    revision: p.revision,
    name: p.name,
    settings: structuredClone(p.settings),
    scenes: structuredClone(p.scenes),
  };
  $("#save-state").textContent = "Guardando…";
  state.saving = api(`/api/projects/${p.id}`, "PUT", payload);
  try {
    const saved = await state.saving;
    if (state.project?.id === saved.id) {
      state.project.revision = saved.revision;
      if (serial === state.serial) {
        state.dirty = false;
        $("#save-state").textContent = "Guardado";
        renderCanvas();
      } else {
        $("#save-state").textContent = "Cambios pendientes";
      }
    }
    return saved;
  } catch (error) {
    $("#save-state").textContent = "Error al guardar";
    throw error;
  } finally {
    state.saving = null;
  }
}
async function pick(kind, target, button) {
  button.disabled = true;
  try {
    const selected = await api("/api/pick", "POST", { kind });
    if (selected.path) $(target).value = selected.path;
  } finally {
    button.disabled = false;
  }
}
function trackJob(job) {
  state.tracked.add(job.id);
  state.jobs = [job, ...state.jobs.filter((j) => j.id !== job.id)];
  state.dock = job.id;
  updateDock();
  pollJobs();
}
function updateDock() {
  const active = state.jobs.filter((j) =>
    ["queued", "running"].includes(j.status),
  );
  let job = active.find((j) => j.id === state.dock) || active[0];
  $("#job-dock").hidden = !job;
  if (!job) return;
  state.dock = job.id;
  $("#job-title").textContent = job.label;
  $("#job-message").textContent = job.message;
  $("#job-percent").textContent = `${Math.round(job.progress)}%`;
  $("#job-progress").style.width = `${job.progress}%`;
  $("#cancel-job").textContent =
    job.status === "queued" ? "Quitar de la cola" : "Cancelar";
}
let polling = false;
async function pollJobs() {
  if (polling) return;
  polling = true;
  try {
    state.jobs = await api("/api/jobs");
    for (const job of state.jobs) {
      if (
        state.tracked.has(job.id) &&
        ["done", "error", "cancelled"].includes(job.status)
      ) {
        state.tracked.delete(job.id);
        if (job.status === "done") await completeJob(job);
        else
          toast(
            job.status === "error" ? job.message : "Tarea cancelada.",
            job.status === "error",
          );
      }
    }
    updateDock();
    if (state.page === "history" && state.historyFilter === "jobs")
      renderHistory();
  } catch (error) {
    if (state.jobs.some((j) => ["running", "queued"].includes(j.status)))
      toast("Se perdió la conexión con la app. Revisa que siga abierta.", true);
  } finally {
    polling = false;
  }
}
async function completeJob(job) {
  if (job.kind === "install") {
    await refreshStatus();
    toast("Motor local de voz instalado.");
    return;
  }
  if (job.kind === "voice") {
    await refreshVoices();
    toast("Voz guardada. Ya puedes crear su ejemplo.");
    return;
  }
  if (job.kind === "voice-preview") {
    await refreshVoices();
    if (job.result.audio_url) {
      const player = new Audio(job.result.audio_url + `?t=${Date.now()}`);
      player.play().catch(() => {});
    }
    toast("Ejemplo de voz listo.");
    return;
  }
  if (job.kind === "audio") {
    if (
      state.project?.id === job.project_id &&
      !state.dirty &&
      state.project.revision === job.result.project_revision
    ) {
      for (const scene of job.result.scenes) state.times[scene.number] = scene;
      renderSceneList();
      renderSceneControls();
    }
    toast("Narración preparada y duraciones medidas.");
    return;
  }
  if (job.kind === "render") {
    await refreshHistory();
    if (state.project?.id === job.project_id) {
      state.videoMode = true;
      const result = job.result;
      if (!state.dirty && state.project.revision === result.project_revision) {
        for (const timing of result.timing || [])
          state.times[timing.number] = timing;
        renderSceneList();
        renderSceneControls();
      }
      $("#video-player").src = result.video_url;
      $("#image-canvas").hidden = true;
      $("#video-player").hidden = false;
      $("#video-player").classList.toggle("vertical", result.aspect === "9:16");
      $("#back-to-image").hidden = false;
      $("#canvas-title").textContent = result.preview
        ? "Vista previa terminada"
        : "Video exportado";
      $("#canvas-caption").textContent =
        `${result.width} × ${result.height} · ${seconds(result.duration)} · ${result.voice_name}`;
      if (state.page === "studio")
        $("#video-player").scrollIntoView({
          behavior: "smooth",
          block: "center",
        });
    }
    toast(
      job.result.preview
        ? "Vista previa lista."
        : "Video exportado y guardado en el historial.",
    );
  }
}
async function startRender({
  preview = false,
  scene = null,
  audio_only = false,
} = {}) {
  if (!state.project) return;
  await saveProject();
  if (!state.project.settings.voice_id)
    throw new Error("Selecciona una voz narradora.");
  const job = await api(`/api/projects/${state.project.id}/render`, "POST", {
    preview,
    scene,
    audio_only,
  });
  trackJob(job);
  toast("Tarea añadida. Puedes seguir revisando tus escenas.");
}
async function refreshStatus() {
  state.status = await api("/api/status");
  $("#hardware-info").textContent =
    `CPU · ${state.status.ram_gb ? `${state.status.ram_gb} GB RAM` : "procesamiento local"}`;
  $("#engine-badge").textContent = state.status.engine.ready
    ? "LISTO · CPU"
    : "CPU";
  $("#engine-note").textContent = state.status.engine.ready
    ? "Motor listo. La síntesis se realiza en tu equipo; en CPU puede tardar más que la narración resultante."
    : state.status.engine.note;
  $("#install-engine").textContent = state.status.engine.ready
    ? "Motor instalado ✓"
    : "Instalar motor local";
  $("#install-engine").disabled = state.status.engine.ready;
}
async function refreshVoices() {
  state.voices = await api("/api/voices");
  renderVoiceSelect();
  renderVoices();
}
function renderVoiceSelect() {
  if (!state.project) return;
  const current = state.project.settings.voice_id;
  const select = $("#voice-select");
  select.replaceChildren(
    element("option", { value: "" }, "Seleccionar voz…"),
    ...state.voices.map((v) =>
      element(
        "option",
        { value: v.id },
        `${v.name}${v.engine === "sapi" ? " · Windows" : ""}`,
      ),
    ),
  );
  select.value = current;
}
function renderVoices() {
  const list = $("#voice-list");
  list.replaceChildren();
  if (!state.voices.length) {
    list.append(
      element(
        "div",
        { class: "empty-state" },
        "Tu biblioteca está vacía. Guarda una muestra de voz para empezar.",
      ),
    );
    return;
  }
  state.voices.forEach((voice, index) => {
    const card = element("article", { class: "voice-card" });
    const top = element("div", { class: "voice-card-top" });
    const name = element("div");
    name.append(
      element("h3", {}, voice.name),
      element(
        "small",
        {},
        `${voice.kind} · ${voice.language}${voice.engine === "chatterbox" ? ` · muestra de ${seconds(voice.sample_duration)}` : ""}`,
      ),
    );
    top.append(
      element(
        "div",
        { class: "voice-avatar" },
        voice.engine === "sapi" ? "◉" : String(index + 1).padStart(2, "0"),
      ),
      name,
    );
    card.append(top);
    if (voice.engine === "chatterbox") {
      card.append(
        element("span", { class: "sample-label" }, "Muestra original"),
        element("audio", {
          controls: "",
          preload: "none",
          src: `/api/voices/${voice.id}/reference`,
        }),
      );
    }
    if (voice.preview) {
      card.append(
        element("span", { class: "sample-label" }, "Ejemplo sintetizado"),
        element("audio", {
          controls: "",
          preload: "none",
          src: voice.preview + `?t=${Date.now()}`,
        }),
      );
    }
    const actions = element("div", { class: "voice-card-actions" });
    actions.append(
      element(
        "button",
        {
          class: "button secondary small",
          onclick: report(() => voiceExample(voice)),
        },
        "▶ Generar ejemplo",
      ),
    );
    if (state.project)
      actions.append(
        element(
          "button",
          {
            class: "text-button",
            onclick: () => {
              state.project.settings.voice_id = voice.id;
              markDirty({ audio: true });
              renderVoiceSelect();
              setPage("studio");
              toast(`Voz seleccionada: ${voice.name}`);
            },
          },
          "Usar en este proyecto",
        ),
      );
    else
      actions.append(
        element(
          "span",
          { class: "muted" },
          voice.engine === "sapi" ? "Sin clonación" : "Perfil local",
        ),
      );
    card.append(actions);
    if (voice.engine === "chatterbox")
      card.append(
        element(
          "button",
          {
            class: "text-button",
            onclick: report(async () => {
              if (
                !confirm(
                  `¿Retirar «${voice.name}» de la biblioteca? Los videos ya generados se conservan.`,
                )
              )
                return;
              await api(`/api/voices/${voice.id}`, "DELETE");
              await refreshVoices();
              toast("Voz retirada de la biblioteca.");
            }),
          },
          "Retirar de la biblioteca",
        ),
      );
    list.append(card);
  });
}
async function voiceExample(voice) {
  if (voice.engine === "chatterbox" && !state.status.engine.ready)
    throw new Error(
      "Instala el motor local para generar ejemplos de las voces clonadas.",
    );
  const job = await api(`/api/voices/${voice.id}/preview`, "POST", {
    text: $("#example-text").value,
  });
  trackJob(job);
}
async function refreshHistory() {
  state.exports = await api("/api/exports");
  if (state.page === "history") renderHistory();
}
function renderHistory() {
  const list = $("#history-list");
  list.replaceChildren();
  if (state.historyFilter === "jobs") {
    state.jobs.forEach((job) => {
      const card = element("article", { class: "job-card" });
      const top = element("div", { class: "job-card-top" });
      const statusNames = {
        done: "Terminado",
        error: "Error",
        running: "En curso",
        queued: "En cola",
        cancelled: "Cancelado",
      };
      top.append(
        element("h3", {}, job.label),
        element(
          "span",
          { class: `job-status ${job.status}` },
          statusNames[job.status],
        ),
      );
      card.append(
        top,
        element("p", {}, job.message),
        element("small", { class: "muted" }, date(job.created)),
      );
      if (["running", "queued"].includes(job.status))
        card.append(
          element(
            "button",
            {
              class: "text-button",
              onclick: report(() =>
                api(`/api/jobs/${job.id}/cancel`, "POST", {}),
              ),
            },
            "Cancelar",
          ),
        );
      list.append(card);
    });
  } else {
    const previews = state.historyFilter === "previews";
    const items = state.exports.filter((e) => !!e.preview === previews);
    items.forEach((item) => {
      const card = element("article", { class: "export-card" });
      const cover = element("button", {
        class: "export-cover",
        onclick: () => watch(item),
        "aria-label": `Reproducir ${item.project_name}`,
      });
      cover.style.border = "0";
      cover.append(
        element("video", {
          src: item.video_url,
          preload: "metadata",
          muted: "",
          style:
            "width:100%;height:100%;object-fit:cover;opacity:.7;pointer-events:none;",
        }),
        element("span", { class: "play" }, "▶"),
        element(
          "span",
          { class: "format" },
          `${item.aspect} · ${item.resolution}`,
        ),
      );
      const details = element("div", { class: "export-details" });
      details.append(
        element("h3", {}, item.project_name),
        element(
          "p",
          {},
          `${seconds(item.duration)} · ${item.scenes} escenas · ${item.width} × ${item.height}`,
        ),
        element("p", {}, `${item.voice_name} · ${date(item.created)}`),
      );
      const actions = element("div", { class: "export-actions" });
      actions.append(
        element(
          "a",
          { class: "text-button", href: item.video_url + "?download=true" },
          "↓ MP4",
        ),
        element(
          "button",
          {
            class: "text-button",
            onclick: report(() =>
              api(`/api/exports/${item.id}/open-folder`, "POST", {}),
            ),
          },
          "Abrir carpeta",
        ),
        element(
          "button",
          {
            class: "text-button",
            onclick: report(() => openProject(item.project_id)),
          },
          "Abrir proyecto",
        ),
      );
      if (item.subtitles_enabled)
        actions.append(
          element(
            "a",
            {
              class: "text-button",
              href: item.subtitles_url + "?download=true",
            },
            "SRT",
          ),
        );
      details.append(actions);
      card.append(cover, details);
      list.append(card);
    });
  }
  if (!list.children.length)
    list.append(
      element(
        "div",
        { class: "empty-state" },
        state.historyFilter === "jobs"
          ? "Aún no hay tareas."
          : "Todavía no hay videos aquí. Genera una vista previa o exporta tu primer proyecto.",
      ),
    );
}
function watch(item) {
  $("#watch-title").textContent = item.project_name;
  $("#watch-meta").textContent =
    `${item.aspect} · ${item.width} × ${item.height} · ${item.voice_name}`;
  $("#history-player").src = item.video_url;
  $("#history-player").className =
    item.aspect === "9:16" ? "vertical" : "horizontal";
  $("#watch-download").href = item.video_url + "?download=true";
  $("#watch-dialog").showModal();
}
function openImport() {
  $("#import-error").textContent = "";
  $("#import-dialog").showModal();
}
async function importProject(event) {
  event.preventDefault();
  const button = $("#import-form button[type=submit]");
  button.disabled = true;
  $("#import-error").textContent = "";
  try {
    const p = await api("/api/projects", "POST", {
      folder: $("#import-folder").value.trim(),
      script: $("#import-script").value.trim(),
      name: $("#import-name").value.trim(),
      pairing: $("#import-pairing").value,
    });
    $("#import-dialog").close();
    await openProject(p.id);
    toast(
      `${p.scenes.length} escenas importadas. Revisa la pareja imagen–texto.`,
    );
  } catch (error) {
    $("#import-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
async function importVoice(event) {
  event.preventDefault();
  const button = $("#voice-form button[type=submit]");
  button.disabled = true;
  $("#voice-error").textContent = "";
  try {
    const job = await api("/api/voices", "POST", {
      name: $("#voice-name").value.trim(),
      reference: $("#voice-reference").value.trim(),
    });
    $("#voice-dialog").close();
    trackJob(job);
    $("#voice-form").reset();
  } catch (error) {
    $("#voice-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
function setupEvents() {
  options($("#scene-motion"), motions);
  options($("#scene-transition"), transitions);
  options($("#scene-filter"), filters);
  $$("[data-page]").forEach(
    (button) => (button.onclick = () => setPage(button.dataset.page)),
  );
  $("#new-project").onclick = openImport;
  $("#new-project-small").onclick = openImport;
  $("#import-form").onsubmit = importProject;
  $("#voice-form").onsubmit = importVoice;
  $$(".close-dialog").forEach(
    (button) => (button.onclick = () => button.closest("dialog").close()),
  );
  $$("[data-pick]").forEach(
    (button) =>
      (button.onclick = report(() =>
        pick(button.dataset.pick, `#${button.dataset.target}`, button),
      )),
  );
  $("#new-voice").onclick = () => {
    $("#voice-error").textContent = "";
    $("#voice-dialog").showModal();
  };
  $("#watch-dialog").addEventListener("close", () =>
    $("#history-player").pause(),
  );
  $("#project-name").oninput = () => {
    state.project.name = $("#project-name").value;
    markDirty();
  };
  $("#save-project").onclick = report(async () => {
    await saveProject();
    await refreshProjects();
    toast("Proyecto guardado.");
  });
  $("#scene-search").oninput = renderSceneList;
  $("#scene-text").oninput = () => {
    selectedScene().text = $("#scene-text").value;
    markDirty({ audio: true });
  };
  for (const [selector, key] of Object.entries({
    "#scene-motion": "motion",
    "#scene-transition": "transition",
    "#scene-filter": "filter",
    "#scene-fit": "fit",
    "#extra-pause": "extra_pause",
    "#focus-x": "focus_x",
    "#focus-y": "focus_y",
  }))
    $(selector).addEventListener(
      selector.startsWith("#focus-") ? "input" : "change",
      () => {
        const value = $(selector).value;
        selectedScene()[key] = ["focus_x", "focus_y", "extra_pause"].includes(
          key,
        )
          ? Number(value)
          : value;
        updateFocusLabels();
        markDirty();
      },
    );
  for (const [selector, key] of Object.entries({
    "#resolution": "resolution",
    "#global-fit": "fit",
    "#fps": "fps",
    "#pause": "pause",
    "#transition-duration": "transition_duration",
    "#speed": "speed",
  }))
    $(selector).onchange = () => {
      state.project.settings[key] = [
        "fps",
        "pause",
        "transition_duration",
        "speed",
      ].includes(key)
        ? Number($(selector).value)
        : $(selector).value;
      state.videoMode = false;
      markDirty({ audio: key === "speed" });
      updateDimensions();
    };
  $("#subtitles").onchange = () => {
    state.project.settings.subtitles = $("#subtitles").checked;
    markDirty();
  };
  $("#voice-select").onchange = () => {
    state.project.settings.voice_id = $("#voice-select").value;
    markDirty({ audio: true });
  };
  $("#manage-voices").onclick = () => setPage("voices");
  $$("[data-aspect]").forEach(
    (button) =>
      (button.onclick = () => {
        state.project.settings.aspect = button.dataset.aspect;
        state.videoMode = false;
        markDirty();
        renderCanvas();
      }),
  );
  $("#apply-filter").onclick = () => {
    const filter = selectedScene().filter;
    state.project.scenes.forEach((s) => (s.filter = filter));
    markDirty();
    toast("Filtro aplicado a todas las escenas.");
  };
  $("#apply-transition").onclick = () => {
    const transition = selectedScene().transition;
    state.project.scenes.forEach((s) => (s.transition = transition));
    markDirty();
    toast("Transición aplicada a todas las escenas.");
  };
  $("#reset-auto").onclick = () => {
    state.project.scenes.forEach((s) => {
      s.motion = "auto";
      s.transition = "auto";
    });
    markDirty();
    renderSceneControls();
    toast("Movimientos y transiciones automáticos.");
  };
  $("#choose-focus").onclick = () => {
    const scene = selectedScene();
    $("#focus-image").src = imageUrl(scene);
    $("#focus-marker").style.left = `${scene.focus_x * 100}%`;
    $("#focus-marker").style.top = `${scene.focus_y * 100}%`;
    $("#focus-dialog").showModal();
  };
  $("#focus-image").onclick = (event) => {
    const rect = event.target.getBoundingClientRect();
    const scene = selectedScene();
    scene.focus_x = Math.max(
      0,
      Math.min(1, (event.clientX - rect.left) / rect.width),
    );
    scene.focus_y = Math.max(
      0,
      Math.min(1, (event.clientY - rect.top) / rect.height),
    );
    $("#focus-marker").style.left = `${scene.focus_x * 100}%`;
    $("#focus-marker").style.top = `${scene.focus_y * 100}%`;
    $("#focus-x").value = scene.focus_x;
    $("#focus-y").value = scene.focus_y;
    updateFocusLabels();
    markDirty();
  };
  $("#preview-scene").onclick = report(() =>
    startRender({ preview: true, scene: selectedScene().number }),
  );
  $("#preview-video").onclick = report(() => startRender({ preview: true }));
  $("#export-video").onclick = report(() => startRender());
  $("#measure-audio").onclick = report(() => startRender({ audio_only: true }));
  $("#back-to-image").onclick = () => {
    state.videoMode = false;
    renderCanvas();
  };
  $("#install-engine").onclick = report(async () => {
    const job = await api("/api/engine/install", "POST", {});
    trackJob(job);
  });
  $("#cancel-job").onclick = report(() =>
    api(`/api/jobs/${state.dock}/cancel`, "POST", {}),
  );
  $("#refresh-history").onclick = report(refreshHistory);
  $$("[data-history]").forEach(
    (button) =>
      (button.onclick = () => {
        state.historyFilter = button.dataset.history;
        $$("[data-history]").forEach((b) =>
          b.classList.toggle("selected", b === button),
        );
        renderHistory();
      }),
  );
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}
async function init() {
  setupEvents();
  await Promise.all([
    refreshProjects(),
    refreshStatus(),
    refreshVoices(),
    refreshHistory(),
  ]);
  state.jobs = await api("/api/jobs");
  state.jobs
    .filter((j) => ["running", "queued"].includes(j.status))
    .forEach((j) => state.tracked.add(j.id));
  updateDock();
  setInterval(pollJobs, 1800);
}
init().catch((error) => toast(`No se pudo conectar: ${error.message}`, true));
