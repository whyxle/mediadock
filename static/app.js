const $ = (selector) => document.querySelector(selector);
const THEME_STORAGE_KEY = "mediadock-theme";

const els = {
  health: $("#healthBadge"),
  themeToggle: $("#themeToggle"),
  urlInput: $("#urlInput"),
  format: $("#formatSelect"),
  audioQuality: $("#audioQualitySelect"),
  videoHeight: $("#videoHeightSelect"),
  playlistMode: $("#playlistModeSelect"),
  cookiesMode: $("#cookiesModeSelect"),
  outputDir: $("#outputDirInput"),
  add: $("#addButton"),
  probe: $("#probeButton"),
  clear: $("#clearButton"),
  refresh: $("#refreshButton"),
  probeBox: $("#probeBox"),
  jobs: $("#jobsList"),
  queueSummary: $("#queueSummary"),
  toast: $("#toast"),
  saveSettings: $("#saveSettingsButton"),
  concurrency: $("#concurrencyInput"),
  defaultCookies: $("#defaultCookiesSelect"),
  pythonPath: $("#pythonPathInput"),
  ffmpegPath: $("#ffmpegPathInput"),
  denoPath: $("#denoPathInput"),
  poServerHome: $("#poServerHomeInput"),
};

const statusLabels = {
  queued: "В очереди",
  running: "Загрузка",
  cancelling: "Отмена",
  cancelled: "Отменено",
  completed: "Готово",
  failed: "Ошибка",
};

let currentSettings = {};
let pollHandle = null;
let toastTimer = null;

function normalizeTheme(theme) {
  return theme === "dark" ? "dark" : "light";
}

function storedTheme() {
  try {
    return normalizeTheme(localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "light";
  }
}

function selectedTheme() {
  return els.themeToggle?.checked ? "dark" : "light";
}

function applyTheme(theme) {
  const normalized = normalizeTheme(theme);
  document.documentElement.dataset.theme = normalized;
  document.documentElement.style.colorScheme = normalized;
  if (els.themeToggle) {
    els.themeToggle.checked = normalized === "dark";
  }
  try {
    localStorage.setItem(THEME_STORAGE_KEY, normalized);
  } catch {
    // Theme still applies for the current page even when localStorage is unavailable.
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function showToast(message) {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.hidden = false;
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, 4200);
}

function durationLabel(seconds) {
  if (!seconds) return "длительность неизвестна";
  const value = Number(seconds);
  const h = Math.floor(value / 3600);
  const m = Math.floor((value % 3600) / 60);
  const s = Math.floor(value % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

function bytesLabel(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function readDownloadPayload() {
  return {
    urls: els.urlInput.value,
    format: els.format.value,
    audioQuality: els.audioQuality.value,
    videoHeight: els.videoHeight.value,
    playlistMode: els.playlistMode.value,
    cookiesMode: els.cookiesMode.value,
    outputDir: els.outputDir.value,
  };
}

function applySettings(settings) {
  currentSettings = settings || {};
  applyTheme(currentSettings.theme || storedTheme());
  els.outputDir.value = currentSettings.outputDir || "";
  els.concurrency.value = currentSettings.concurrency || 2;
  els.defaultCookies.value = currentSettings.defaultCookiesMode || "off";
  els.cookiesMode.value = currentSettings.defaultCookiesMode || "off";
  els.pythonPath.value = currentSettings.pythonPath || "";
  els.ffmpegPath.value = currentSettings.ffmpegPath || "";
  els.denoPath.value = currentSettings.denoPath || "";
  els.poServerHome.value = currentSettings.poServerHome || "";
}

async function saveTheme(theme) {
  const normalized = normalizeTheme(theme);
  applyTheme(normalized);
  if (els.themeToggle) {
    els.themeToggle.disabled = true;
  }
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ theme: normalized }),
    });
    currentSettings = data.settings || { ...currentSettings, theme: normalized };
    applyTheme(currentSettings.theme || normalized);
    showToast(normalized === "dark" ? "Тёмная тема включена." : "Светлая тема включена.");
  } catch (error) {
    showToast(`Не удалось сохранить тему: ${error.message}`);
  } finally {
    if (els.themeToggle) {
      els.themeToggle.disabled = false;
    }
  }
}

async function loadHealth() {
  try {
    const data = await api("/api/health");
    applySettings(data.settings);
    els.health.className = `health ${data.ok ? "ok" : "bad"}`;
    const ready = data.ok ? "готовы" : "нужна проверка";
    els.health.textContent = `Инструменты ${ready}: yt-dlp ${data.tools.ytDlp.version || ""}`;
  } catch (error) {
    els.health.className = "health bad";
    els.health.textContent = `Health недоступен: ${error.message}`;
  }
}

async function loadJobs() {
  try {
    const data = await api("/api/jobs");
    renderJobs(data.jobs || []);
  } catch (error) {
    showToast(error.message);
  }
}

function renderJobs(jobs) {
  const active = jobs.filter((job) => ["queued", "running", "cancelling"].includes(job.status)).length;
  const completed = jobs.filter((job) => job.status === "completed").length;
  els.queueSummary.textContent = jobs.length
    ? `${active} активных, ${completed} завершенных, всего ${jobs.length}`
    : "Задач пока нет.";

  if (!jobs.length) {
    els.jobs.innerHTML = '<div class="empty">Добавьте ссылку, и здесь появится прогресс загрузки.</div>';
    return;
  }

  els.jobs.innerHTML = jobs.map(renderJob).join("");
  els.jobs.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => handleJobAction(button.dataset.action, button.dataset.id));
  });
}

function renderJob(job) {
  const title = escapeHtml(job.title || job.url);
  const status = escapeHtml(statusLabels[job.status] || job.status);
  const pct = Math.max(0, Math.min(100, Number(job.progress || 0)));
  const meta = [
    job.format?.toUpperCase(),
    job.audioQuality && job.format && ["mp3", "m4a", "wav", "flac"].includes(job.format) ? `audio ${job.audioQuality}` : "",
    job.videoHeight && !["mp3", "m4a", "wav", "flac"].includes(job.format) ? `video ${job.videoHeight}` : "",
    job.speed || "",
    job.eta ? `ETA ${job.eta}` : "",
    job.cookiesMode && job.cookiesMode !== "off" ? `cookies ${job.cookiesMode}` : "",
  ].filter(Boolean);
  const canCancel = ["queued", "running", "cancelling"].includes(job.status);
  const canOpen = job.status === "completed";
  const retryCookies = job.canRetryWithCookies ? `<button class="small-button" data-action="retryCookies" data-id="${job.id}">Повтор с cookies</button>` : "";
  return `
    <article class="job">
      <div class="job-main">
        <div>
          <div class="job-title">${title}</div>
          <div class="job-url">${escapeHtml(job.url)}</div>
        </div>
        <div class="job-badge ${escapeHtml(job.status)}">${status}</div>
      </div>
      <div class="progress-track"><div class="progress-bar" style="width:${pct}%"></div></div>
      <div class="job-meta">${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
      ${job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : ""}
      <div class="job-actions">
        ${canCancel ? `<button class="small-button danger-button" data-action="cancel" data-id="${job.id}">Отменить</button>` : ""}
        ${job.status === "failed" || job.status === "cancelled" ? `<button class="small-button" data-action="retry" data-id="${job.id}">Повторить</button>` : ""}
        ${retryCookies}
        ${canOpen ? `<button class="small-button" data-action="openFile" data-id="${job.id}">Открыть файл</button>` : ""}
        ${canOpen ? `<button class="small-button" data-action="openFolder" data-id="${job.id}">Открыть папку</button>` : ""}
      </div>
    </article>
  `;
}

async function handleJobAction(action, id) {
  try {
    if (action === "cancel") {
      await api(`/api/jobs/${id}/cancel`, { method: "POST", body: "{}" });
      showToast("Задача отменяется.");
    }
    if (action === "retry") {
      await api(`/api/jobs/${id}/retry`, { method: "POST", body: "{}" });
      showToast("Задача добавлена заново.");
    }
    if (action === "retryCookies") {
      const mode = els.cookiesMode.value === "off" ? "firefox" : els.cookiesMode.value;
      await api(`/api/jobs/${id}/retry`, { method: "POST", body: JSON.stringify({ cookiesMode: mode }) });
      showToast(`Повтор с cookies: ${mode}`);
    }
    if (action === "openFile") {
      await api(`/api/jobs/${id}/open-file`, { method: "POST", body: "{}" });
    }
    if (action === "openFolder") {
      await api(`/api/jobs/${id}/open-folder`, { method: "POST", body: "{}" });
    }
    await loadJobs();
  } catch (error) {
    showToast(error.message);
  }
}

async function addJobs() {
  els.add.disabled = true;
  try {
    const payload = readDownloadPayload();
    const data = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
    showToast(`Добавлено задач: ${data.jobs.length}`);
    await loadJobs();
  } catch (error) {
    showToast(error.message);
  } finally {
    els.add.disabled = false;
  }
}

async function probeLink() {
  const firstUrl = els.urlInput.value.split(/\s+/).find(Boolean);
  if (!firstUrl) {
    showToast("Вставьте ссылку для проверки.");
    return;
  }
  els.probe.disabled = true;
  els.probeBox.hidden = false;
  els.probeBox.innerHTML = "Получаю информацию...";
  try {
    const data = await api("/api/probe", {
      method: "POST",
      body: JSON.stringify({
        url: firstUrl,
        playlistMode: els.playlistMode.value,
        cookiesMode: els.cookiesMode.value,
      }),
    });
    renderProbe(data.info);
  } catch (error) {
    els.probeBox.innerHTML = `<div class="job-error">${escapeHtml(error.message)}</div>`;
  } finally {
    els.probe.disabled = false;
  }
}

function renderProbe(info) {
  const sampleFormats = (info.formats || [])
    .filter((item) => item.ext)
    .slice(-8)
    .map((item) => `${item.ext}${item.resolution ? ` ${item.resolution}` : ""}${item.filesize ? ` ${bytesLabel(item.filesize)}` : ""}`);
  els.probeBox.innerHTML = `
    <p class="probe-title">${escapeHtml(info.title || "Без названия")}</p>
    <div class="probe-meta">
      <span>${escapeHtml(info.extractor || "источник")}</span>
      <span>${durationLabel(info.duration)}</span>
      <span>${info.playlistCount ? `элементов плейлиста: ${info.playlistCount}` : "одно видео"}</span>
      <span>форматов: ${(info.formats || []).length}</span>
    </div>
    ${sampleFormats.length ? `<div class="probe-meta">${sampleFormats.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
  `;
}

async function saveSettings() {
  els.saveSettings.disabled = true;
  try {
    const payload = {
      outputDir: els.outputDir.value,
      concurrency: Number(els.concurrency.value || 2),
      defaultCookiesMode: els.defaultCookies.value,
      pythonPath: els.pythonPath.value,
      ffmpegPath: els.ffmpegPath.value,
      denoPath: els.denoPath.value,
      poServerHome: els.poServerHome.value,
      theme: selectedTheme(),
    };
    const data = await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    applySettings(data.settings);
    await loadHealth();
    showToast("Настройки сохранены.");
  } catch (error) {
    showToast(error.message);
  } finally {
    els.saveSettings.disabled = false;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function wireEvents() {
  els.add.addEventListener("click", addJobs);
  els.probe.addEventListener("click", probeLink);
  els.clear.addEventListener("click", () => {
    els.urlInput.value = "";
    els.probeBox.hidden = true;
  });
  els.refresh.addEventListener("click", loadJobs);
  els.saveSettings.addEventListener("click", saveSettings);
  els.themeToggle.addEventListener("change", () => saveTheme(selectedTheme()));
  els.format.addEventListener("change", () => {
    const audio = ["mp3", "m4a", "wav", "flac"].includes(els.format.value);
    els.audioQuality.disabled = !audio;
    els.videoHeight.disabled = audio;
  });
}

async function boot() {
  applyTheme(storedTheme());
  wireEvents();
  await loadHealth();
  await loadJobs();
  els.format.dispatchEvent(new Event("change"));
  pollHandle = setInterval(loadJobs, 1200);
  window.addEventListener("beforeunload", () => clearInterval(pollHandle));
}

boot();
