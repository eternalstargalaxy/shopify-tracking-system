(() => {
  const tokenInput = document.querySelector("#token");
  const carrierInput = document.querySelector("#carrier");
  const numsInput = document.querySelector("#nums");
  const runButton = document.querySelector("#run-query");
  const exportButton = document.querySelector("#export-csv");
  const refreshRecentButton = document.querySelector("#refresh-recent");
  const errorBox = document.querySelector("#error-message");
  const resultsTable = document.querySelector("#results-table");
  const recentList = document.querySelector("#recent-list");
  const queryCount = document.querySelector("#query-count");
  const cacheCount = document.querySelector("#cache-count");
  const errorCount = document.querySelector("#error-count");

  let latestRows = [];
  tokenInput.value = window.localStorage.getItem("internal-dashboard-token") || "";

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));

  const setError = (message) => {
    errorBox.hidden = !message;
    errorBox.textContent = message || "";
  };

  const readToken = () => {
    const token = tokenInput.value.trim();
    if (!token) {
      throw new Error("请先输入内部查询令牌。");
    }
    window.localStorage.setItem("internal-dashboard-token", token);
    return token;
  };

  const formatTime = (value) => {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return escapeHtml(value);
    return date.toLocaleString("zh-CN", { hour12: false });
  };

  const renderTimeline = (events) => {
    if (!events?.length) {
      return '<span class="cell-sub">暂无详细轨迹</span>';
    }
    return `
      <details>
        <summary>查看轨迹（${events.length}）</summary>
        <ol class="timeline">
          ${events.map((event) => `
            <li>
              <div class="cell-main">${escapeHtml(event.description || "No description")}</div>
              <div class="cell-sub">${formatTime(event.eventTime || event.time)} ${escapeHtml(event.location || "")}</div>
            </li>
          `).join("")}
        </ol>
      </details>
    `;
  };

  const renderResults = (payload) => {
    const shipments = payload.shipments || [];
    const errors = payload.errors || [];
    latestRows = shipments.map((shipment) => ({
      trackingNumber: shipment.trackingNumber,
      carrier: shipment.carrierName || shipment.carrierCode || "",
      status: shipment.statusText || shipment.normalizedStatus || "",
      providerStatus: shipment.providerStatusDescription || shipment.providerStatus || "",
      updatedAt: shipment.updatedAt || "",
      cached: shipment.cached ? "yes" : "no",
    }));

    queryCount.textContent = String(payload.queryCount || shipments.length);
    cacheCount.textContent = String(shipments.filter((item) => item.cached).length);
    errorCount.textContent = String(errors.length);

    if (!shipments.length && !errors.length) {
      resultsTable.innerHTML = '<div class="empty-state">没有返回结果，请检查物流单号格式。</div>';
      return;
    }

    resultsTable.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Tracking</th>
              <th>Carrier</th>
              <th>Status</th>
              <th>Updated</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            ${shipments.map((shipment) => `
              <tr>
                <td>
                  <div class="cell-main">${escapeHtml(shipment.trackingNumber)}</div>
                  <div class="cell-sub">${escapeHtml(shipment.originCountry || "")} ${shipment.originCountry && shipment.destinationCountry ? "->" : ""} ${escapeHtml(shipment.destinationCountry || "")}</div>
                </td>
                <td>
                  <div class="cell-main">${escapeHtml(shipment.carrierName || shipment.carrierCode || "Pending")}</div>
                  <div class="cell-sub">${shipment.cached ? "缓存命中" : "实时查询"}</div>
                </td>
                <td>
                  <span class="pill ${escapeHtml(shipment.normalizedStatus || "unknown")}">${escapeHtml(shipment.statusText || shipment.normalizedStatus || "Unknown")}</span>
                  <div class="cell-sub">${escapeHtml(shipment.providerStatusDescription || shipment.supportNotice || "")}</div>
                </td>
                <td>${formatTime(shipment.updatedAt)}</td>
                <td>${renderTimeline(shipment.events)}</td>
              </tr>
            `).join("")}
            ${errors.map((error) => `
              <tr>
                <td><div class="cell-main">${escapeHtml(error.trackingNumber)}</div></td>
                <td>-</td>
                <td><span class="pill exception">Query error</span></td>
                <td>-</td>
                <td><span class="cell-sub">${escapeHtml(error.message)}</span></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  };

  const fetchRecent = async () => {
    try {
      const token = readToken();
      const response = await fetch("/internal/api/recent?limit=12", {
        headers: { "x-internal-token": token },
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "无法加载最近查询。");
      }
      const shipments = payload.shipments || [];
      recentList.innerHTML = shipments.length
        ? shipments.map((shipment) => `
            <article class="recent-item">
              <strong>${escapeHtml(shipment.trackingNumber)}</strong>
              <div class="recent-meta">${escapeHtml(shipment.carrierName || shipment.carrierCode || "Pending")} · ${escapeHtml(shipment.statusText || shipment.normalizedStatus || "Unknown")}</div>
              <div class="recent-meta">${formatTime(shipment.updatedAt)}</div>
            </article>
          `).join("")
        : '<div class="empty-state">还没有缓存记录。</div>';
    } catch (error) {
      recentList.innerHTML = `<div class="empty-state">${escapeHtml(error.message || "无法加载最近查询。")}</div>`;
    }
  };

  const runQuery = async () => {
    try {
      setError("");
      const token = readToken();
      resultsTable.innerHTML = '<div class="empty-state">正在查询，请稍候...</div>';
      const response = await fetch("/internal/api/track", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-internal-token": token,
        },
        body: JSON.stringify({
          nums: numsInput.value,
          carrier: carrierInput.value.trim() || null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.errors?.[0]?.message || "查询失败。");
      }
      renderResults(payload);
      fetchRecent();
    } catch (error) {
      resultsTable.innerHTML = '<div class="empty-state">查询未完成，请修正后重试。</div>';
      setError(error.message || "查询失败。");
    }
  };

  const exportCsv = () => {
    if (!latestRows.length) {
      setError("当前没有可导出的查询结果。");
      return;
    }
    const lines = [
      ["tracking_number", "carrier", "status", "provider_status", "updated_at", "cached"].join(","),
      ...latestRows.map((row) => [
        row.trackingNumber,
        row.carrier,
        row.status,
        row.providerStatus,
        row.updatedAt,
        row.cached,
      ].map((value) => `"${String(value || "").replace(/"/g, "\"\"")}"`).join(",")),
    ];
    const blob = new Blob([`\ufeff${lines.join("\n")}`], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `tracking-export-${Date.now()}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  runButton.addEventListener("click", runQuery);
  exportButton.addEventListener("click", exportCsv);
  refreshRecentButton.addEventListener("click", fetchRecent);
  tokenInput.addEventListener("change", fetchRecent);

  if (tokenInput.value) {
    fetchRecent();
  }
})();
