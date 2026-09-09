(function () {
  "use strict";

  var CONFIG_ID = "oplot-mpr-config";
  var ROOT_ID = "oplotMprRoot";

  function isLocalUrl(value) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return false;
    try { return new URL(value, window.location.origin).origin === window.location.origin; }
    catch (_error) { return false; }
  }

  function parseConfig() {
    var element = document.getElementById(CONFIG_ID);
    if (!element) throw new Error("mpr_configuration_error");
    var config;
    try { config = JSON.parse(element.textContent || "{}"); }
    catch (_error) { throw new Error("mpr_configuration_error"); }
    if (!config || !config.urls || !isLocalUrl(config.urls.preview) || !isLocalUrl(config.urls.generate)) {
      throw new Error("mpr_configuration_error");
    }
    return config;
  }

  function initMprPage() {
    var root = document.getElementById(ROOT_ID);
    if (!root || root.dataset.mprInitialized === "true") return;
    root.dataset.mprInitialized = "true";

    var fileInput = document.getElementById("mprFiles");
    var fileList = document.getElementById("mprFileList");
    var templateCodeInput = document.getElementById("mprTemplateCode");
    var alertBox = document.getElementById("mprAlert");
    var statusBox = document.getElementById("mprStatus");
    var generateBtn = document.getElementById("mprGenerateBtn");
    var packageModal = document.getElementById("mprPackageModal");
    var packageSummary = document.getElementById("mprPackageSummary");
    var packageOptions = document.getElementById("mprPackageOptions");
    var unmappedBox = document.getElementById("mprUnmapped");
    var confirmGenerateBtn = document.getElementById("mprConfirmGenerateBtn");
    var confirmGenerateLabel = document.getElementById("mprConfirmGenerateLabel");
    var resultRoot = document.getElementById("mprResult");
    var downloadAgainBtn = document.getElementById("mprDownloadAgainBtn");
    var newGenerationBtn = document.getElementById("mprNewGenerationBtn");
    var config;
    var selectedTemplateCode = "";
    var selectedTemplateName = "";
    var mprPreview = null;
    var resultObjectUrl = "";
    var resultFilename = "";
    var modalReturnFocus = null;

    function setAlert(message, details) {
      alertBox.replaceChildren();
      if (!message) { alertBox.hidden = true; return; }
      var title = document.createElement("strong");
      title.textContent = String(message);
      alertBox.appendChild(title);
      if (Array.isArray(details) && details.length) {
        var list = document.createElement("ul");
        details.forEach(function (item) {
          var row = document.createElement("li");
          row.textContent = String(item);
          list.appendChild(row);
        });
        alertBox.appendChild(list);
      }
      alertBox.hidden = false;
    }

    function setLoading(loading, message) {
      root.setAttribute("aria-busy", loading ? "true" : "false");
      generateBtn.disabled = loading || !selectedTemplateCode;
      statusBox.classList.toggle("is-loading", loading);
      statusBox.textContent = message || "";
    }

    function formatBytes(size) {
      var bytes = Number(size || 0);
      if (bytes < 1024) return bytes + " Б";
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " КиБ";
      return (bytes / (1024 * 1024)).toFixed(1) + " МиБ";
    }

    function renderFileList() {
      fileList.replaceChildren();
      var files = Array.from(fileInput.files || []);
      if (!files.length) {
        var empty = document.createElement("div");
        empty.className = "mpr-empty-files";
        empty.textContent = "Файлы пока не выбраны.";
        fileList.appendChild(empty);
        return;
      }
      files.forEach(function (file) {
        var row = document.createElement("div");
        row.className = "mpr-file-row";
        var name = document.createElement("strong");
        name.textContent = file.name;
        var size = document.createElement("span");
        size.textContent = formatBytes(file.size);
        row.appendChild(name);
        row.appendChild(size);
        fileList.appendChild(row);
      });
    }

    function findTemplate(code) {
      return Array.from(document.querySelectorAll("[data-template-code]")).find(function (item) {
        return item.dataset.templateCode === code;
      });
    }

    function selectTemplate(code, name) {
      selectedTemplateCode = String(code || "");
      selectedTemplateName = String(name || selectedTemplateCode);
      templateCodeInput.value = selectedTemplateCode;
      document.querySelectorAll("[data-template-code]").forEach(function (item) {
        var active = item.dataset.templateCode === selectedTemplateCode;
        item.classList.toggle("is-active", active);
        if (item.tagName === "BUTTON") item.setAttribute("aria-pressed", active ? "true" : "false");
      });
      mprPreview = null;
      setAlert("");
    }

    function buildFormData(files) {
      var formData = new FormData();
      formData.append("template_code", selectedTemplateCode);
      files.forEach(function (file) { formData.append("files", file, file.name); });
      return formData;
    }

    function showModal() {
      modalReturnFocus = document.activeElement;
      packageModal.hidden = false;
      document.body.classList.add("mpr-modal-open");
      var first = packageModal.querySelector("input:not(:disabled), button:not(:disabled)");
      if (first) first.focus();
    }

    function hideModal() {
      if (packageModal.hidden) return;
      packageModal.hidden = true;
      document.body.classList.remove("mpr-modal-open");
      if (modalReturnFocus && document.contains(modalReturnFocus)) modalReturnFocus.focus();
      modalReturnFocus = null;
    }

    function selectedPackageCodes() {
      return Array.from(packageOptions.querySelectorAll('.mpr-package-checkbox:checked')).map(function (item) { return item.value; });
    }

    function packageLabel(code) {
      var match = (mprPreview && mprPreview.packages || []).find(function (item) { return item.code === code; });
      return match ? match.label : code;
    }

    function updateConfirmState() {
      var count = selectedPackageCodes().length;
      var hasUnmapped = Boolean((mprPreview && mprPreview.unmapped || []).length);
      confirmGenerateBtn.disabled = count === 0 || hasUnmapped;
      confirmGenerateLabel.textContent = count > 1 ? "Сформировать пакет (" + count + ")" : count === 1 ? "Сформировать DOCX (1)" : "Выберите документ";
    }

    function renderPackageSelection(preview, filesCount) {
      packageSummary.textContent = "Файлов: " + filesCount + " · хостов после обработки: " + (preview.rows_count || 0);
      packageOptions.replaceChildren();
      (preview.packages || []).forEach(function (item) {
        var disabled = !item.available;
        var label = document.createElement("label");
        label.className = "mpr-package-option " + (disabled ? "is-disabled" : "is-selected");
        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "packages";
        checkbox.className = "mpr-package-checkbox";
        checkbox.value = String(item.code || "");
        checkbox.disabled = disabled;
        checkbox.checked = !disabled;
        var copy = document.createElement("span");
        var name = document.createElement("strong");
        name.textContent = String(item.label || item.code || "");
        var datacenters = document.createElement("small");
        datacenters.textContent = (item.datacenters || []).join(" · ");
        copy.appendChild(name);
        copy.appendChild(datacenters);
        var count = document.createElement("span");
        count.className = "mpr-package-count";
        count.textContent = (item.rows_count || 0) + " хостов";
        label.appendChild(checkbox);
        label.appendChild(copy);
        label.appendChild(count);
        checkbox.addEventListener("change", function () {
          label.classList.toggle("is-selected", checkbox.checked);
          updateConfirmState();
        });
        packageOptions.appendChild(label);
      });

      unmappedBox.replaceChildren();
      var unmapped = preview.unmapped || [];
      if (unmapped.length) {
        var warning = document.createElement("strong");
        warning.textContent = "Есть хосты с нераспределённым значением ЦОД:";
        var list = document.createElement("ul");
        unmapped.forEach(function (item) {
          var row = document.createElement("li");
          row.textContent = String(item.datacenter) + ": " + item.rows_count + " строк";
          list.appendChild(row);
        });
        unmappedBox.appendChild(warning);
        unmappedBox.appendChild(list);
        unmappedBox.hidden = false;
      } else {
        unmappedBox.hidden = true;
      }
      updateConfirmState();
    }

    async function readErrorPayload(response) {
      var contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) return await response.json();
      return { error: "Сервис вернул непредвиденный ответ" };
    }

    function getDownloadFilename(disposition) {
      var utfMatch = String(disposition || "").match(/filename\*=UTF-8''([^;]+)/i);
      if (utfMatch) {
        try { return decodeURIComponent(utfMatch[1]); } catch (_error) { return ""; }
      }
      var match = String(disposition || "").match(/filename="?([^";]+)"?/i);
      return match ? match[1] : "";
    }

    function revokeResultUrl() {
      if (!resultObjectUrl) return;
      URL.revokeObjectURL(resultObjectUrl);
      resultObjectUrl = "";
    }

    function triggerDownload(url, filename) {
      var link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    }

    async function downloadResponse(response, selectedPackages) {
      var blob = await response.blob();
      var disposition = response.headers.get("content-disposition") || "";
      var contentType = response.headers.get("content-type") || "";
      var isZip = contentType.includes("application/zip");
      resultFilename = getDownloadFilename(disposition) || (isZip ? "mpr.zip" : "mpr.docx");
      revokeResultUrl();
      resultObjectUrl = URL.createObjectURL(blob);
      triggerDownload(resultObjectUrl, resultFilename);
      document.getElementById("mprResultFilename").textContent = resultFilename;
      document.getElementById("mprResultType").textContent = isZip ? "ZIP" : "DOCX";
      document.getElementById("mprResultTemplate").textContent = selectedTemplateName || selectedTemplateCode;
      document.getElementById("mprResultSources").textContent = Array.from(fileInput.files || []).map(function (file) { return file.name; }).join(", ");
      document.getElementById("mprResultRows").textContent = String(mprPreview && mprPreview.rows_count || 0);
      document.getElementById("mprResultPackages").textContent = selectedPackages.map(packageLabel).join(", ");
      resultRoot.hidden = false;
      resultRoot.scrollIntoView({ block: "nearest", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }

    function resetGeneration() {
      revokeResultUrl();
      resultFilename = "";
      resultRoot.hidden = true;
      fileInput.value = "";
      mprPreview = null;
      packageOptions.replaceChildren();
      unmappedBox.hidden = true;
      setAlert("");
      setLoading(false, "");
      var initial = findTemplate(config.initial_template_code);
      selectTemplate(config.initial_template_code, initial && initial.dataset.templateName);
      renderFileList();
      fileInput.focus();
    }

    try {
      config = parseConfig();
      selectedTemplateCode = String(config.initial_template_code || "");
    } catch (_error) {
      config = null;
      generateBtn.disabled = true;
      confirmGenerateBtn.disabled = true;
      setAlert("Страница МПР настроена некорректно. Обновите страницу или обратитесь к администратору.");
      renderFileList();
      return;
    }

    document.querySelectorAll("button[data-template-code]").forEach(function (button) {
      button.addEventListener("click", function () { selectTemplate(button.dataset.templateCode, button.dataset.templateName); });
    });
    var initialTemplate = findTemplate(selectedTemplateCode);
    selectTemplate(selectedTemplateCode, initialTemplate && initialTemplate.dataset.templateName);

    fileInput.addEventListener("change", function () {
      renderFileList();
      mprPreview = null;
      resultRoot.hidden = true;
      revokeResultUrl();
      setAlert("");
    });

    generateBtn.addEventListener("click", async function () {
      setAlert("");
      var files = Array.from(fileInput.files || []);
      if (!selectedTemplateCode) return setAlert("Не выбран шаблон");
      if (!files.length) return setAlert("Не загружены файлы");
      setLoading(true, "Проверяем состав комплектов…");
      try {
        var response = await fetch(config.urls.preview, { method: "POST", body: buildFormData(files) });
        if (!response.ok) throw await readErrorPayload(response);
        mprPreview = await response.json();
        renderPackageSelection(mprPreview, files.length);
        setLoading(false, "Данные проверены. Выберите комплекты для формирования.");
        showModal();
      } catch (error) {
        setLoading(false, "");
        setAlert(error && error.error || "Не удалось проверить данные МПР", error && error.details || []);
      }
    });

    confirmGenerateBtn.addEventListener("click", async function () {
      var selectedPackages = selectedPackageCodes();
      if (!selectedPackages.length || Boolean((mprPreview && mprPreview.unmapped || []).length)) return updateConfirmState();
      var files = Array.from(fileInput.files || []);
      var formData = buildFormData(files);
      selectedPackages.forEach(function (code) { formData.append("packages", code); });
      confirmGenerateBtn.disabled = true;
      confirmGenerateLabel.textContent = "Формируем…";
      root.setAttribute("aria-busy", "true");
      try {
        var response = await fetch(config.urls.generate, { method: "POST", body: formData });
        if (!response.ok) throw await readErrorPayload(response);
        await downloadResponse(response, selectedPackages);
        hideModal();
        statusBox.textContent = selectedPackages.length > 1 ? "Пакет МПР сформирован." : "DOCX МПР сформирован.";
        setAlert("");
      } catch (error) {
        hideModal();
        statusBox.textContent = "";
        setAlert(error && error.error || "Не удалось сформировать документы МПР", error && error.details || []);
      } finally {
        root.setAttribute("aria-busy", "false");
        updateConfirmState();
      }
    });

    packageModal.querySelectorAll("[data-modal-close]").forEach(function (button) { button.addEventListener("click", hideModal); });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !packageModal.hidden) hideModal(); });
    downloadAgainBtn.addEventListener("click", function () { if (resultObjectUrl) triggerDownload(resultObjectUrl, resultFilename); });
    newGenerationBtn.addEventListener("click", resetGeneration);
    window.addEventListener("pagehide", revokeResultUrl, { once: true });
    renderFileList();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initMprPage, { once: true });
  else initMprPage();
})();
