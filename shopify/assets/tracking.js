(function () {
  const config = window.TRACKING_CONFIG || {};
  const apiEndpoint = config.apiEndpoint || "/apps/track/api/track";

  const form = document.querySelector("#trackingForm");
  const textarea = document.querySelector("#trackingNumbers");
  const carrierSelect = document.querySelector("#carrierSelect");
  const message = document.querySelector("#formMessage");
  const resultsList = document.querySelector("#resultsList");
  const emptyState = document.querySelector("#emptyState");
  const template = document.querySelector("#shipmentTemplate");
  const trackButton = document.querySelector("#trackBtn");

  const STATUS_LABELS = {
    info_received: "Info received",
    in_transit: "In transit",
    out_for_delivery: "Out for delivery",
    delivered: "Delivered",
    exception: "Exception",
    failed_attempt: "Delivery attempt failed",
    not_found: "No tracking updates",
    expired: "Tracking expired",
    unknown: "Unknown"
  };
  const PROGRESS_ORDER = ["info_received", "in_transit", "out_for_delivery", "delivered"];

  function setMessage(text, isError) {
    message.textContent = text || "";
    message.classList.toggle("is-error", Boolean(isError));
  }

  function parseTrackingNumbers(value) {
    const matches = value.match(/[a-z0-9]{6,42}/gi) || [];
    return [...new Set(matches.map((item) => item.toUpperCase()))].slice(0, 40);
  }

  function formatDate(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  }

  function renderShipments(shipments) {
    resultsList.innerHTML = "";
    emptyState.hidden = shipments.length > 0;

    shipments.forEach((shipment) => {
      const node = template.content.firstElementChild.cloneNode(true);
      node.querySelector(".carrier-name").textContent = shipment.carrierName || "Auto";
      node.querySelector(".tracking-number").textContent = shipment.trackingNumber;

      const statusPill = node.querySelector(".status-pill");
      statusPill.textContent = shipment.statusText || STATUS_LABELS[shipment.normalizedStatus] || "Unknown";
      statusPill.classList.add(`is-${shipment.normalizedStatus}`);

      node.querySelector(".updated-at").textContent = formatDate(shipment.updatedAt);
      node.querySelector(".origin").textContent = shipment.originCountry || "-";
      node.querySelector(".destination").textContent = shipment.destinationCountry || "-";
      node.querySelector(".support-notice").textContent = shipment.supportNotice || "";
      node.dataset.status = shipment.normalizedStatus || "unknown";

      const providerStatus = node.querySelector(".provider-status");
      const providerText = [shipment.providerStatus, shipment.providerStatusDescription]
        .filter(Boolean)
        .join(" · ");
      providerStatus.textContent = providerText;
      providerStatus.hidden = !providerText;

      const activeIndex = PROGRESS_ORDER.indexOf(shipment.normalizedStatus);
      node.querySelectorAll(".shipment-progress span").forEach((step, index) => {
        step.classList.toggle("is-active", index <= activeIndex && activeIndex >= 0);
      });

      const timeline = node.querySelector(".timeline");
      const events = shipment.events || [];
      if (!events.length) {
        const item = document.createElement("li");
        item.className = "empty-event";
        item.innerHTML = `
          <time>-</time>
          <div class="event-text">
            <div class="event-title">No tracking timeline is available yet.</div>
            <div class="event-location"></div>
          </div>
        `;
        timeline.appendChild(item);
      }
      events.forEach((event) => {
        const item = document.createElement("li");
        const eventTime = event.eventTime || event.time;
        const eventStatus = event.providerStatus || event.raw_status || "";
        item.innerHTML = `
          <time>${escapeHtml(formatDate(eventTime))}</time>
          <div class="event-text">
            <div class="event-title">${escapeHtml(event.description || "")}</div>
            <div class="event-location">${escapeHtml([event.location, eventStatus].filter(Boolean).join(" · "))}</div>
          </div>
        `;
        timeline.appendChild(item);
      });

      resultsList.appendChild(node);
    });
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function queryTracking(numbers) {
    const params = new URLSearchParams();
    params.set("nums", numbers.join(","));
    if (carrierSelect.value) params.set("carrier", carrierSelect.value);

    const response = await fetch(`${apiEndpoint}?${params.toString()}`, {
      headers: { Accept: "application/json" }
    });
    if (!response.ok) {
      throw new Error(`Request failed with ${response.status}`);
    }
    return response.json();
  }

  async function submitQuery() {
    const numbers = parseTrackingNumbers(textarea.value);
    if (!numbers.length) {
      setMessage("Please enter at least one valid tracking number.", true);
      return;
    }

    trackButton.disabled = true;
    setMessage(`Tracking ${numbers.length} shipment(s)...`);

    try {
      const data = await queryTracking(numbers);
      renderShipments(data.shipments || []);
      if (data.success === false && !(data.shipments || []).length) {
        setMessage((data.errors && data.errors[0] && data.errors[0].message) || "No shipment data was returned.", true);
      } else if (data.errors && data.errors.length) {
        setMessage(data.errors[0].message || "Some shipments failed to load.", true);
      } else {
        setMessage(`Loaded ${data.shipments.length} shipment(s).`);
      }
    } catch (error) {
      setMessage("Tracking lookup failed. Please try again later.", true);
      emptyState.hidden = false;
      resultsList.innerHTML = "";
      console.error(error);
    } finally {
      trackButton.disabled = false;
    }
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      submitQuery();
    });
  }

  const url = new URL(window.location.href);
  const nums = url.searchParams.get("nums");
  if (nums && textarea) {
    textarea.value = nums;
    submitQuery();
  }
})();
