function showChartMessage(canvas, message) {
    const parent = canvas.parentElement;
    if (!parent) return;
    parent.innerHTML = '<div class="chart-empty-state">' + escapeHtml(message) + "</div>";
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}



document.addEventListener("DOMContentLoaded", function () {
    initMonthlyReleasesChart();
});

function initMonthlyReleasesChart() {
    const canvas = document.getElementById("monthlyReleasesChart");

    if (!canvas) {
        console.warn("Canvas #monthlyReleasesChart not found");
        return;
    }

    if (typeof Chart === "undefined") {
        console.error("Chart.js is not loaded");
        showChartMessage(canvas, "Chart.js не подключён.");
        return;
    }

    const rawLabelsAttr = canvas.getAttribute("data-labels") || "[]";
    const rawValuesAttr = canvas.getAttribute("data-values") || "[]";

    let finalLabels = [];
    let finalValues = [];

    try {
        finalLabels = JSON.parse(rawLabelsAttr);
    } catch (error) {
        console.error("Ошибка чтения labels:", error, rawLabelsAttr);
        showChartMessage(canvas, "Ошибка чтения labels для графика.");
        return;
    }

    try {
        finalValues = JSON.parse(rawValuesAttr);
    } catch (error) {
        console.error("Ошибка чтения values:", error, rawValuesAttr);
        showChartMessage(canvas, "Ошибка чтения values для графика.");
        return;
    }

    if (!Array.isArray(finalLabels) || finalLabels.length === 0) {
        showChartMessage(canvas, "Нет данных по месяцам для построения графика.");
        return;
    }

    if (!Array.isArray(finalValues)) {
        finalValues = [];
    }

    finalValues = finalValues.map(function (item) {
        const num = Number(item);
        return Number.isFinite(num) ? num : 0;
    });

    while (finalValues.length < finalLabels.length) {
        finalValues.push(0);
    }

    if (finalValues.length > finalLabels.length) {
        finalValues = finalValues.slice(0, finalLabels.length);
    }

    const ctx = canvas.getContext("2d");

    if (window.monthlyReleasesChartInstance) {
        window.monthlyReleasesChartInstance.destroy();
    }

    window.monthlyReleasesChartInstance = new Chart(ctx, {
        type: "bar",
        data: {
            labels: finalLabels,
            datasets: [
                {
                    label: "Количество релизов",
                    data: finalValues,
                    backgroundColor: "rgba(76, 132, 255, 0.55)",
                    borderColor: "rgba(76, 132, 255, 1)",
                    borderWidth: 1,
                    borderRadius: 8,
                    maxBarThickness: 42,
                    hoverBackgroundColor: "rgba(76, 132, 255, 0.8)"
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
                
            onClick: function (event, activeElements) {
                if (activeElements && activeElements.length > 0) {
                    const index = activeElements[0].index;
                    const selectedMonth = this.data.labels[index];

                    if (selectedMonth) {
                        const releasesOnlyUrl = canvas.getAttribute("data-releases-only-url");
                        const targetUrl = releasesOnlyUrl + "?month=" + encodeURIComponent(selectedMonth);
                        window.open(targetUrl, "_blank");
                    }
                }
            },
            plugins: {
                legend: {
                    display: true,
                    position: "top",
                    labels: {
                        color: "#d9e1f2"
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return " Релизов: " + context.parsed.y;
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: "#aebbd2",
                        maxRotation: 45,
                        minRotation: 0
                    },
                    grid: {
                        color: "rgba(255,255,255,0.05)"
                    }
                },
                y: {
                    beginAtZero: true,
                    ticks: {
                        color: "#aebbd2",
                        precision: 0
                    },
                    grid: {
                        color: "rgba(255,255,255,0.08)"
                    }
                }
            }
        }
    });
}

function showChartMessage(canvas, message) {
    const parent = canvas.parentElement;

    if (!parent) {
        return;
    }

    parent.innerHTML = '<div class="chart-empty-state">' + escapeHtml(message) + "</div>";
}

function escapeHtml(value) {
    return String(value)
        .split("&").join("&amp;")
        .split("<").join("&lt;")
        .split(">").join("&gt;")
        .split('"').join("&quot;")
        .split("'").join("&#39;");
}
