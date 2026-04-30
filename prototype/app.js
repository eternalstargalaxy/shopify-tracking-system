const USER_CONFIG = window.TRACKING_CONFIG || {};
const CONFIG = {
  apiEndpoint: USER_CONFIG.apiEndpoint || "/api/track",
  demoMode: USER_CONFIG.demoMode !== undefined ? USER_CONFIG.demoMode : true,
};

const STATUS_LABELS = {
  transit: "运输中",
  delivered: "已妥投",
  exception: "异常",
  pending: "待查询",
};

const STATUS_PROGRESS = {
  transit: "66%",
  delivered: "100%",
  exception: "48%",
  pending: "18%",
};

const els = {
  form: document.querySelector("#trackingForm"),
  textarea: document.querySelector("#trackingNumbers"),
  carrier: document.querySelector("#carrierSelect"),
  locale: document.querySelector("#localeSelect"),
  count: document.querySelector("#numberCount"),
  message: document.querySelector("#formMessage"),
  trackBtn: document.querySelector("#trackBtn"),
  clearBtn: document.querySelector("#clearBtn"),
  results: document.querySelector("#resultsList"),
  empty: document.querySelector("#emptyState"),
  template: document.querySelector("#shipmentTemplate"),
  todayCount: document.querySelector("#todayCount"),
  copySummary: document.querySelector("#copySummaryBtn"),
  expandAll: document.querySelector("#expandAllBtn"),
  collapseAll: document.querySelector("#collapseAllBtn"),
  chips: [...document.querySelectorAll(".carrier-chips button")],
  summaries: [...document.querySelectorAll("[data-summary]")],
};

let lastShipments = [];

function parseTrackingNumbers(value) {
  const matches = value.match(/[a-z0-9]{6,42}/gi) || [];
  return [...new Set(matches.map((item) => item.toUpperCase()))].slice(0, 40);
}

function updateNumberCount() {
  const count = parseTrackingNumbers(els.textarea.value).length;
  els.count.textContent = String(count);
  return count;
}

function setMessage(text, type = "info") {
  els.message.textContent = text;
  els.message.classList.toggle("is-error", type === "error");
}

function setLoading(isLoading) {
  els.trackBtn.disabled = isLoading;
  els.trackBtn.querySelector("span").textContent = isLoading ? "查询中" : "查询";
}

function getCarrierName(value) {
  const option = [...els.carrier.options].find((item) => item.value === value);
  return option ? option.textContent : "自动识别";
}

function updateActiveCarrier() {
  els.chips.forEach((chip) => {
    chip.classList.toggle("is-active", chip.dataset.carrier === els.carrier.value);
  });
}

async function queryShipments(numbers) {
  const payload = {
    locale: els.locale.value,
    trackingNumbers: numbers.map((number) => ({
      number,
      carrier: els.carrier.value || null,
      carrierHint: getCarrierName(els.carrier.value),
    })),
  };

  if (CONFIG.demoMode || location.protocol === "file:") {
    await wait(520);
    return {
      shipments: numbers.map((number, index) =>
        createDemoShipment(number, index, getCarrierName(els.carrier.value)),
      ),
    };
  }

  const response = await fetch(CONFIG.apiEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }

  return response.json();
}

function createDemoShipment(number, index, carrierName) {
  const statusOrder = ["transit", "delivered", "pending", "exception"];
  const status = statusOrder[index % statusOrder.length];
  const now = Date.now();
  const base = now - (index + 2) * 86400000;
  const origin = inferOrigin(number, index);
  const destination = inferDestination(index);

  const eventSets = {
    transit: [
      ["到达目的地分拨中心", destination, now - 3 * 3600000],
      ["航班落地，等待清关", "Los Angeles, US", now - 18 * 3600000],
      ["离开发件地机场", origin, base + 24 * 3600000],
      ["承运商已揽收", origin, base],
    ],
    delivered: [
      ["已签收", destination, now - 4 * 3600000],
      ["派送中", destination, now - 8 * 3600000],
      ["到达末端站点", destination, now - 19 * 3600000],
      ["离开发件地分拨中心", origin, base],
    ],
    pending: [
      ["已创建物流单", origin, now - 2 * 3600000],
      ["等待承运商接收", origin, now - 4 * 3600000],
    ],
    exception: [
      ["地址信息需要确认", destination, now - 2 * 3600000],
      ["到达目的地分拨中心", destination, now - 9 * 3600000],
      ["清关完成", "Customs", now - 26 * 3600000],
      ["承运商已揽收", origin, base],
    ],
  };

  const days = Math.max(1, Math.ceil((now - base) / 86400000));

  return {
    trackingNumber: number,
    carrierName: carrierName === "自动识别" ? inferCarrier(number, index) : carrierName,
    status,
    statusText: STATUS_LABELS[status],
    origin,
    destination,
    updatedAt: formatDateTime(now - index * 2700000),
    eta: status === "delivered" ? "已送达" : formatDate(Date.now() + (index + 2) * 86400000),
    days: `${days} 天`,
    events: eventSets[status].map(([description, location, time]) => ({
      description,
      location,
      time: formatDateTime(time),
    })),
  };
}

function inferCarrier(number, index) {
  if (/US$/.test(number)) return "USPS";
  if (/CN$/.test(number)) return "China Post";
  if (/^\d{10}$/.test(number)) return "DHL Express";
  return ["YunExpress", "FedEx", "UPS", "4PX"][index % 4];
}

function inferOrigin(number, index) {
  if (/CN$/.test(number)) return "Shenzhen, CN";
  if (/US$/.test(number)) return "New York, US";
  return ["Guangzhou, CN", "Hong Kong, CN", "Tokyo, JP", "Frankfurt, DE"][index % 4];
}

function inferDestination(index) {
  return ["Los Angeles, US", "Berlin, DE", "Sydney, AU", "Madrid, ES"][index % 4];
}

function formatDate(timestamp) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(timestamp);
}

function formatDateTime(timestamp) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(timestamp);
}

function wait(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function renderShipments(shipments) {
  els.results.innerHTML = "";
  lastShipments = shipments;

  shipments.forEach((shipment) => {
    const node = els.template.content.firstElementChild.cloneNode(true);
    node.dataset.status = shipment.status;
    node.querySelector(".carrier-name").textContent = shipment.carrierName || "自动识别";
    node.querySelector(".status-pill").textContent =
      shipment.statusText || STATUS_LABELS[shipment.status] || "运输中";
    node.querySelector(".tracking-number").textContent = shipment.trackingNumber;
    node.querySelector(".origin").textContent = shipment.origin || "-";
    node.querySelector(".destination").textContent = shipment.destination || "-";
    node.querySelector(".updated-at").textContent = shipment.updatedAt || "-";
    node.querySelector(".eta").textContent = shipment.eta || "-";
    node.querySelector(".days").textContent = shipment.days || "-";
    node
      .querySelector(".route-progress")
      .style.setProperty("--progress", STATUS_PROGRESS[shipment.status] || "60%");

    const timeline = node.querySelector(".timeline");
    (shipment.events || []).forEach((event) => {
      const item = document.createElement("li");
      item.innerHTML = `
        <time class="timeline-time">${escapeHtml(event.time || "")}</time>
        <div>
          <div class="timeline-title">${escapeHtml(event.description || "")}</div>
          <div class="timeline-location">${escapeHtml(event.location || "")}</div>
        </div>
      `;
      timeline.appendChild(item);
    });

    node.querySelector(".copy-button").addEventListener("click", () => {
      copyText(formatShipmentSummary(shipment));
      setMessage("已复制当前运单结果");
    });

    node.querySelector(".toggle-details").addEventListener("click", (event) => {
      const collapsed = node.classList.toggle("is-collapsed");
      event.currentTarget.querySelector("span").textContent = collapsed ? "展开轨迹" : "收起轨迹";
    });

    els.results.appendChild(node);
  });

  els.empty.hidden = shipments.length > 0;
  updateSummary(shipments);
}

function updateSummary(shipments) {
  const counts = shipments.reduce(
    (acc, shipment) => {
      acc[shipment.status] = (acc[shipment.status] || 0) + 1;
      return acc;
    },
    { transit: 0, delivered: 0, exception: 0, pending: 0 },
  );

  els.summaries.forEach((summary) => {
    summary.textContent = String(counts[summary.dataset.summary] || 0);
  });

  els.todayCount.textContent = String(shipments.length);
}

function formatShipmentSummary(shipment) {
  const lines = [
    `${shipment.trackingNumber} - ${shipment.statusText || STATUS_LABELS[shipment.status]}`,
    `Carrier: ${shipment.carrierName || "-"}`,
    `Route: ${shipment.origin || "-"} -> ${shipment.destination || "-"}`,
    `Updated: ${shipment.updatedAt || "-"}`,
  ];

  (shipment.events || []).forEach((event) => {
    lines.push(`${event.time || ""} ${event.location || ""} ${event.description || ""}`.trim());
  });

  return lines.join("\n");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function copyText(text) {
  if (!text) return;

  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

els.textarea.addEventListener("input", () => {
  const count = updateNumberCount();
  if (count === 40) {
    setMessage("已达到单次查询上限");
  } else if (els.message.classList.contains("is-error")) {
    setMessage("");
  }
});

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const numbers = parseTrackingNumbers(els.textarea.value);
  updateNumberCount();

  if (numbers.length === 0) {
    setMessage("请输入有效运单号", "error");
    els.textarea.focus();
    return;
  }

  setLoading(true);
  setMessage(`正在查询 ${numbers.length} 个运单`);

  try {
    const data = await queryShipments(numbers);
    renderShipments(Array.isArray(data.shipments) ? data.shipments : []);
    setMessage(`查询完成：${numbers.length} 个运单`);
  } catch (error) {
    setMessage("查询失败，请稍后重试", "error");
    console.error(error);
  } finally {
    setLoading(false);
  }
});

els.clearBtn.addEventListener("click", () => {
  els.textarea.value = "";
  updateNumberCount();
  setMessage("");
  renderShipments([]);
  els.textarea.focus();
});

els.carrier.addEventListener("change", updateActiveCarrier);

els.chips.forEach((chip) => {
  chip.addEventListener("click", () => {
    els.carrier.value = chip.dataset.carrier;
    updateActiveCarrier();
  });
});

els.copySummary.addEventListener("click", async () => {
  if (!lastShipments.length) {
    setMessage("暂无可复制结果");
    return;
  }

  await copyText(lastShipments.map(formatShipmentSummary).join("\n\n"));
  setMessage("已复制查询摘要");
});

els.expandAll.addEventListener("click", () => {
  document.querySelectorAll(".shipment-card").forEach((card) => {
    card.classList.remove("is-collapsed");
    card.querySelector(".toggle-details span").textContent = "收起轨迹";
  });
});

els.collapseAll.addEventListener("click", () => {
  document.querySelectorAll(".shipment-card").forEach((card) => {
    card.classList.add("is-collapsed");
    card.querySelector(".toggle-details span").textContent = "展开轨迹";
  });
});

updateNumberCount();
updateActiveCarrier();
