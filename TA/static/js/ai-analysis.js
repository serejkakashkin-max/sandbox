(function () {
    "use strict";

    const POLL_INTERVAL_MS = 2500;

    function setFeedback(button, message, isError) {
        const panel = button.closest("[data-ai-panel]");
        const feedback = panel ? panel.querySelector("[data-ai-feedback]") : null;
        if (feedback) {
            feedback.textContent = message || "";
            feedback.classList.toggle("d-none", !message);
            feedback.classList.toggle("ai-feedback--error", Boolean(isError));
        }
    }

    function setRunning(button) {
        button.disabled = true;
        button.dataset.aiStatus = "running";
        button.textContent = "Анализируется…";
        setFeedback(button, "GigaChat изучает сведения инцидента. Можно продолжать пользоваться страницей.", false);
    }

    function openReport(button) {
        const detailUrl = button.dataset.aiDetailUrl;
        if (detailUrl) {
            window.location.assign(detailUrl);
        } else {
            window.location.reload();
        }
    }

    async function readJson(response) {
        try {
            return await response.json();
        } catch (error) {
            return {status: "failed", message: "Сервер вернул неизвестный ответ."};
        }
    }

    async function pollStatus(button) {
        const statusUrl = button.dataset.aiStatusUrl;
        if (!statusUrl) return;
        try {
            const response = await fetch(statusUrl, {
                method: "GET",
                credentials: "same-origin",
                headers: {"Accept": "application/json"}
            });
            const data = await readJson(response);
            if (data.status === "completed") {
                openReport(button);
                return;
            }
            if (data.status === "failed") {
                button.disabled = false;
                button.dataset.aiStatus = "failed";
                button.textContent = "Повторить AI-анализ";
                setFeedback(button, data.message || "AI-анализ завершился ошибкой.", true);
                return;
            }
            if (data.status === "running") {
                window.setTimeout(function () { pollStatus(button); }, POLL_INTERVAL_MS);
            }
        } catch (error) {
            button.disabled = false;
            button.textContent = "Проверить состояние";
            setFeedback(button, "Не удалось получить состояние AI-анализа.", true);
        }
    }

    async function runAnalysis(button) {
        const force = button.dataset.aiForce === "1";
        if (force && !window.confirm("Повторно отправить этот инцидент в GigaChat?")) {
            return;
        }

        const body = new URLSearchParams();
        body.set("csrf_token", button.dataset.csrfToken || "");
        if (force) body.set("force", "1");
        setRunning(button);

        try {
            const response = await fetch(button.dataset.aiUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
                },
                body: body.toString()
            });
            const data = await readJson(response);
            if (data.status === "completed" || data.status === "cached") {
                openReport(button);
                return;
            }
            if (data.status === "running") {
                window.setTimeout(function () { pollStatus(button); }, POLL_INTERVAL_MS);
                return;
            }
            button.disabled = false;
            button.dataset.aiStatus = "failed";
            button.textContent = "Повторить AI-анализ";
            setFeedback(button, data.message || "Не удалось выполнить AI-анализ.", true);
        } catch (error) {
            button.disabled = false;
            button.dataset.aiStatus = "failed";
            button.textContent = "Повторить AI-анализ";
            setFeedback(button, "Не удалось связаться с приложением.", true);
        }
    }

    document.addEventListener("click", function (event) {
        const button = event.target.closest(".ai-action[data-ai-url]");
        if (!button || button.disabled) return;
        event.preventDefault();
        runAnalysis(button);
    });

    document.querySelectorAll('.ai-action[data-ai-status="running"]').forEach(function (button) {
        window.setTimeout(function () { pollStatus(button); }, POLL_INTERVAL_MS);
    });
})();
