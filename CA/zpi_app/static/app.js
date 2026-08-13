const input = document.querySelector("[data-file-input]");
const summary = document.querySelector("[data-file-summary]");
const dropZone = document.querySelector("[data-drop-zone]");
const form = document.querySelector("[data-analysis-form]");
const submit = document.querySelector("[data-submit]");

function syncThemeControls() {
  const dark = window.OplotTheme && window.OplotTheme.current() === "dark";
  document.querySelectorAll("[data-oplot-theme-toggle]").forEach((button) => {
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    const label = button.querySelector(".theme-label");
    if (label) label.textContent = dark ? "Светлая тема" : "Тёмная тема";
  });
}

document.querySelectorAll("[data-oplot-theme-toggle]").forEach((button) => {
  button.addEventListener("click", () => window.OplotTheme?.toggle());
});
document.addEventListener("oplot:themechange", syncThemeControls);
syncThemeControls();

function updateFileSummary() {
  if (!input || !summary) return;
  const files = Array.from(input.files || []);
  if (!files.length) {
    summary.textContent = "Файлы не выбраны";
    return;
  }
  const total = files.reduce((sum, file) => sum + file.size, 0);
  const megabytes = (total / 1024 / 1024).toFixed(1);
  summary.textContent = `${files.length} файл(а) · ${megabytes} МБ`;
}

input?.addEventListener("change", updateFileSummary);
["dragenter", "dragover"].forEach((eventName) => {
  dropZone?.addEventListener(eventName, () => dropZone.classList.add("is-dragging"));
});
["dragleave", "drop"].forEach((eventName) => {
  dropZone?.addEventListener(eventName, () => dropZone.classList.remove("is-dragging"));
});

form?.addEventListener("submit", () => {
  if (!submit) return;
  submit.disabled = true;
  submit.querySelector("span").textContent = "Анализируем…";
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copyTarget);
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.textContent);
      button.textContent = "Скопировано";
      window.setTimeout(() => (button.textContent = "Скопировать"), 1800);
    } catch (_error) {
      button.textContent = "Выделите текст вручную";
    }
  });
});

document.querySelectorAll("[data-reveal-target]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.getElementById(button.dataset.revealTarget);
    if (!target) return;
    target.classList.remove("is-hidden");
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    button.textContent = "ЗПИ сформирована";
    button.disabled = true;
  });
});

if (window.location.hash === "#results") {
  document.getElementById("results")?.focus();
}
