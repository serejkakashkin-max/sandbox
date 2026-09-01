(function () {
    "use strict";

    function setupTheme() {
        const button = document.querySelector("[data-theme-toggle]");
        if (!button) return;
        function sync() {
            const dark = document.documentElement.dataset.theme === "dark";
            button.setAttribute("aria-pressed", String(dark));
            button.title = dark ? "Включить светлую тему" : "Включить тёмную тему";
        }
        button.addEventListener("click", function () {
            const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
            document.documentElement.dataset.theme = next;
            localStorage.setItem("incident-theme", next);
            sync();
        });
        sync();
    }

    function setupUpload() {
        const input = document.querySelector("[data-upload-input]");
        const form = document.querySelector("[data-upload-form]");
        if (!input || !form) return;
        input.addEventListener("change", function () {
            if (input.files && input.files.length) form.requestSubmit();
        });
    }

    function setupConfirmations() {
        document.querySelectorAll("form[data-confirm]").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                if (!window.confirm(form.dataset.confirm || "Продолжить?")) event.preventDefault();
            });
        });
    }

    function setupCopyLink() {
        document.querySelectorAll("[data-copy-dashboard-link]").forEach(function (button) {
            button.addEventListener("click", async function () {
                const url = button.dataset.copyUrl || window.location.href;
                try {
                    await navigator.clipboard.writeText(url);
                    button.textContent = "Ссылка скопирована";
                } catch (error) {
                    window.prompt("Скопируйте ссылку", url);
                }
            });
        });
    }

    function setupRows() {
        const interactive = "a, button, input, select, textarea, summary, details, label, form";
        document.querySelectorAll("[data-incident-row]").forEach(function (row) {
            row.addEventListener("click", function (event) {
                if (event.target.closest(interactive)) return;
                const url = row.dataset.detailUrl;
                if (url) window.location.assign(url);
            });
        });
    }

    function setupBulkSelection() {
        const bar = document.querySelector("[data-bulk-bar]");
        if (!bar) return;
        const count = bar.querySelector("#selectedCount");
        const ids = bar.querySelector("#bulkIds");
        function update() {
            const checked = Array.from(document.querySelectorAll(".bulk-check:checked"));
            bar.classList.toggle("d-none", checked.length === 0);
            if (count) count.textContent = "Выбрано: " + checked.length;
            if (!ids) return;
            ids.replaceChildren();
            checked.forEach(function (checkbox) {
                const input = document.createElement("input");
                input.type = "hidden";
                input.name = "ids";
                input.value = checkbox.value;
                ids.appendChild(input);
            });
        }
        document.addEventListener("change", function (event) {
            if (event.target.matches(".bulk-check")) update();
        });
        update();
    }

    function setupPeriodOverflow() {
        const navigation = document.querySelector("[data-period-navigation]");
        if (!navigation) return;
        const more = navigation.querySelector("[data-period-more]");
        const overflow = navigation.querySelector("[data-period-overflow]");
        const items = Array.from(navigation.querySelectorAll("[data-period-item]"));
        if (!more || !overflow || !items.length) return;

        items.forEach(function (item) {
            item.dataset.periodWidth = String(Math.ceil(item.getBoundingClientRect().width || 150));
        });

        function layout() {
            items.forEach(function (item) {
                item.classList.remove("dropdown-item", "period-overflow-link");
                item.classList.add("period-tab");
                navigation.insertBefore(item, more);
            });
            overflow.replaceChildren();
            more.classList.remove("d-none");

            const fixed = Array.from(navigation.querySelectorAll("[data-period-fixed]"));
            const fixedWidth = fixed.reduce(function (sum, item) {
                return sum + item.getBoundingClientRect().width;
            }, 0);
            const gaps = Math.max(0, fixed.length + items.length) * 8;
            let available = navigation.clientWidth - fixedWidth - more.getBoundingClientRect().width - gaps;
            const active = items.find(function (item) { return item.classList.contains("is-active"); });
            const visible = new Set();

            if (active) {
                visible.add(active);
                available -= Number(active.dataset.periodWidth || 150);
            }
            items.forEach(function (item) {
                if (visible.has(item)) return;
                const width = Number(item.dataset.periodWidth || 150);
                if (available >= width) {
                    visible.add(item);
                    available -= width;
                }
            });

            items.forEach(function (item) {
                if (visible.has(item)) {
                    navigation.insertBefore(item, more);
                } else {
                    item.classList.remove("period-tab");
                    item.classList.add("dropdown-item", "period-overflow-link");
                    overflow.appendChild(item);
                }
            });
            more.classList.toggle("d-none", overflow.children.length === 0);
        }

        let resizeTimer;
        window.addEventListener("resize", function () {
            window.clearTimeout(resizeTimer);
            resizeTimer = window.setTimeout(layout, 100);
        });
        layout();
    }

    setupTheme();
    setupUpload();
    setupConfirmations();
    setupCopyLink();
    setupRows();
    setupBulkSelection();
    setupPeriodOverflow();
})();
