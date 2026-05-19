(function () {
  const config = window.TRACKING_CONFIG || {};
  const apiEndpoint = config.apiEndpoint || "/apps/track/api/track";
  const orderApiEndpoint = apiEndpoint.replace(/\/api\/track$/, "/api/order-track");

  const form = document.querySelector("#trackingForm");
  const textarea = document.querySelector("#trackingNumbers");
  const orderNumberInput = document.querySelector("#orderNumber");
  const modeButtons = Array.from(document.querySelectorAll(".tracking-mode-button"));
  const modePanels = Array.from(document.querySelectorAll("[data-mode-panel]"));
  const message = document.querySelector("#formMessage");
  const resultsList = document.querySelector("#resultsList");
  const emptyState = document.querySelector("#emptyState");
  const shipmentTabs = document.querySelector("#shipmentTabs");
  const sharedOrderSummaryPanel = document.querySelector("#sharedOrderSummaryPanel");
  const template = document.querySelector("#shipmentTemplate");
  const trackButton = document.querySelector("#trackBtn");
  const TRACK_COOLDOWN_SECONDS = 15;
  const DELIVERY_SUPPORT_SPLIT = /(\.\s*For Delivery Issues.*$)/i;
  const TIMELINE_PINNED_RECENT_COUNT = 2;
  const TIMELINE_PINNED_EARLIEST_COUNT = 1;

  const STATUS_LABELS = {
    info_received: "Info received",
    in_transit: "In transit",
    out_for_delivery: "Out for delivery",
    delivered: "Delivered",
    exception: "Exception",
    failed_attempt: "Delivery attempt failed",
    not_found: "Carrier updates pending",
    expired: "Tracking expired",
    unknown: "Carrier updates pending"
  };
  const PROGRESS_ORDER = ["info_received", "in_transit", "out_for_delivery", "delivered"];
  const ORIGIN_LOCATION_KEYWORDS = [
    "mainland china",
    "china, cn",
    "cn",
    "shenzhen",
    "guangzhou",
    "dongguan",
    "origin facility",
    "origin international airport"
  ];
  const PRE_DISPATCH_KEYWORDS = [
    "shipment information received",
    "label created",
    "information received"
  ];
  const STATUS_SENTENCES = {
    info_received: "Your order has been dispatched.",
    in_transit: "Your order is in transit.",
    out_for_delivery: "Your order is out for delivery.",
    delivered: "Your order has been delivered.",
    exception: "There is an issue with this delivery.",
    failed_attempt: "A delivery attempt was unsuccessful.",
    not_found: "The carrier has not shared tracking updates for this parcel yet.",
    expired: "This tracking record has expired.",
    unknown: "The carrier has not shared tracking updates for this parcel yet."
  };
  const DATE_FORMATTER = new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
  const COUNTRY_FORMATTER = typeof Intl !== "undefined" && typeof Intl.DisplayNames === "function"
    ? new Intl.DisplayNames(["en"], { type: "region" })
    : null;
  let cooldownTimer = null;
  let cooldownRemaining = 0;
  let queryMode = "tracking";
  let activeShipmentIndex = 0;

  function setMessage(text, isError) {
    message.textContent = text || "";
    message.classList.toggle("is-error", Boolean(isError));
  }

  function setQueryMode(nextMode) {
    queryMode = nextMode === "order" ? "order" : "tracking";
    modeButtons.forEach((button) => {
      const isActive = button.dataset.mode === queryMode;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    modePanels.forEach((panel) => {
      panel.hidden = panel.dataset.modePanel !== queryMode;
    });
  }

  function parseTrackingNumbers(value) {
    const matches = value.match(/[a-z0-9]{6,42}/gi) || [];
    return [...new Set(matches.map((item) => item.toUpperCase()))].slice(0, 40);
  }

  function looksLikeOrderNumber(value) {
    const text = (value || "").trim().toUpperCase();
    return /^(?:[A-Z]{2,6}\d{3,8}|#\d{3,8})$/.test(text);
  }

  function setTrackButtonLabel() {
    if (!trackButton) return;
    if (trackButton.disabled && cooldownRemaining > 0) {
      trackButton.textContent = `Track again in ${cooldownRemaining}s`;
      return;
    }
    trackButton.textContent = "Track parcel";
  }

  function startCooldown() {
    if (!trackButton) return;
    if (cooldownTimer) {
      window.clearInterval(cooldownTimer);
      cooldownTimer = null;
    }

    cooldownRemaining = TRACK_COOLDOWN_SECONDS;
    trackButton.disabled = true;
    setTrackButtonLabel();

    cooldownTimer = window.setInterval(() => {
      cooldownRemaining -= 1;
      if (cooldownRemaining <= 0) {
        window.clearInterval(cooldownTimer);
        cooldownTimer = null;
        cooldownRemaining = 0;
        trackButton.disabled = false;
      }
      setTrackButtonLabel();
    }, 1000);
  }

  function formatDate(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return DATE_FORMATTER.format(date);
  }

  function normalizeDisplayText(value) {
    if (value == null) return "";
    const text = typeof value === "string" ? value.trim() : String(value).trim();
    if (!text || text === "[object Object]") return "";
    const compact = text.toLowerCase().replace(/[\s_-]+/g, "");
    if (compact.startsWith("notshipped") || compact === "null") {
      return "";
    }
    if ((text.startsWith("{") && text.includes("country")) || text.includes("'country': None")) {
      return "";
    }
    return text;
  }

  function getPrimaryShipmentLabel(shipment) {
    return normalizeDisplayText(shipment.trackingNumber)
      || normalizeDisplayText(shipment.orderSummary && shipment.orderSummary.orderName)
      || normalizeDisplayText(shipment.statusText)
      || "Tracking pending";
  }

  function getSummarySentence(shipment) {
    const providerStatus = normalizeDisplayText(shipment.providerStatus).toLowerCase();
    if (providerStatus === "not shipped") {
      return "This order has not been shipped yet.";
    }
    return STATUS_SENTENCES[shipment.normalizedStatus] || "Tracking updates are available below.";
  }

  function isCarrierUpdatePending(shipment, events) {
    return Boolean(
      shipment
      && !events.length
      && ["unknown", "not_found"].includes(shipment.normalizedStatus)
    );
  }

  function getSupportNoticeText(shipment, events) {
    const supportText = extractSupportText(shipment.providerStatusDescription)
      || extractSupportText(shipment.statusText)
      || shipment.supportNotice
      || "";
    if (supportText) return supportText;
    if (isCarrierUpdatePending(shipment, events)) {
      return "The tracking number was found, but the carrier has not shared any scan events yet.";
    }
    return "";
  }

  function formatCountryName(value) {
    const text = normalizeDisplayText(value).toUpperCase();
    if (!text) return "";
    if (/^[A-Z]{2}$/.test(text) && COUNTRY_FORMATTER) {
      return COUNTRY_FORMATTER.of(text) || text;
    }
    return normalizeDisplayText(value);
  }

  function formatLocation(value, shipment) {
    const destinationCountry = formatCountryName(shipment && shipment.destinationCountry);
    if (!value) return "";
    if (typeof value === "object") {
      const address = value.address || value;
      const parts = [
        normalizeDisplayText(address.city),
        normalizeDisplayText(address.state || address.province || address.region),
        formatCountryName(address.country),
        address.postal_code
      ].filter(Boolean);
      return parts.join(", ");
    }
    const locationText = normalizeDisplayText(value);
    if (!locationText) return destinationCountry;
    if (!destinationCountry) return locationText;
    const lowerLocation = locationText.toLowerCase();
    const lowerCountry = destinationCountry.toLowerCase();
    if (lowerLocation.includes(lowerCountry)) return locationText;
    return `${locationText}, ${destinationCountry}`;
  }

  function isOriginLocationText(value) {
    const text = normalizeDisplayText(value).toLowerCase();
    if (!text) return false;
    return ORIGIN_LOCATION_KEYWORDS.some((keyword) => text.includes(keyword));
  }

  function isPreDispatchDescription(value) {
    const text = normalizeDisplayText(value).toLowerCase();
    if (!text) return false;
    return PRE_DISPATCH_KEYWORDS.some((keyword) => text.includes(keyword));
  }

  function getDispatchEvent(events) {
    const chronological = [...events].reverse();
    return chronological.find((event) => !isPreDispatchDescription(event.description)) || chronological[0] || null;
  }

  function collapseOriginEvents(events, shipment) {
    if (!events.length) return { events: [], hiddenOriginCount: 0, dispatchEvent: null };

    const chronological = [...events].reverse();
    let collapsed = [];
    let hiddenOriginCount = 0;
    let summaryInserted = false;

    chronological.forEach((event, index) => {
      const locationText = formatLocation(event.location, shipment);
      const isOriginEvent = isOriginLocationText(locationText);
      if (isOriginEvent) {
        hiddenOriginCount += 1;
      }

      if (isOriginEvent) {
        return;
      }

      if (!summaryInserted && hiddenOriginCount > 0) {
        const dispatchSource = chronological
          .slice(0, index)
          .find((candidate) => !isPreDispatchDescription(candidate.description))
          || chronological[index - 1]
          || chronological[0];

        collapsed.push({
          time: dispatchSource && (dispatchSource.eventTime || dispatchSource.time),
          eventTime: dispatchSource && (dispatchSource.eventTime || dispatchSource.time),
          description: "Shipment dispatched from our warehouse",
          location: "",
          providerStatus: "",
          raw_status: ""
        });
        summaryInserted = true;
      }

      collapsed.push({
        ...event,
        location: locationText
      });
    });

    if (!summaryInserted && hiddenOriginCount > 0) {
      const dispatchSource = chronological.find((candidate) => !isPreDispatchDescription(candidate.description)) || chronological[0];
      collapsed.push({
        time: dispatchSource && (dispatchSource.eventTime || dispatchSource.time),
        eventTime: dispatchSource && (dispatchSource.eventTime || dispatchSource.time),
        description: "Shipment dispatched from our warehouse",
        location: "",
        providerStatus: "",
        raw_status: ""
      });
      summaryInserted = true;
    }

    const dispatchEvent = getDispatchEvent(chronological);
    return {
      events: collapsed.reverse(),
      hiddenOriginCount,
      dispatchEvent
    };
  }

  function formatStatusText(value) {
    if (!value) return "";
    return STATUS_LABELS[value] || String(value)
      .replaceAll("_", " ")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function cleanEventDescription(value) {
    const text = normalizeDisplayText(value);
    if (!text) return "";
    return text.replace(DELIVERY_SUPPORT_SPLIT, ".").replace(/\s+\.$/, ".").trim();
  }

  function extractSupportText(value) {
    const text = normalizeDisplayText(value);
    if (!text) return "";
    const match = text.match(/For Delivery Issues.*$/i);
    return match ? match[0].trim() : "";
  }

  function renderOrderSummaryPanel(panel, orderSummary) {
    const container = panel && panel.querySelector(".order-summary");
    const orderPanelOrder = panel && panel.querySelector(".order-panel-order");
    if (!container) return;
    if (!orderSummary || (!orderSummary.orderName && !orderSummary.placedAt && !orderSummary.fulfillmentStatus && !(orderSummary.items || []).length)) {
      container.hidden = true;
      if (panel) panel.hidden = true;
      if (orderPanelOrder) orderPanelOrder.hidden = true;
      return;
    }

    const orderName = normalizeDisplayText(orderSummary.orderName);
    const placedAt = orderSummary.placedAt ? formatDate(orderSummary.placedAt) : "";
    const fulfilment = normalizeDisplayText(orderSummary.fulfillmentStatus);
    const items = Array.isArray(orderSummary.items) ? orderSummary.items : [];

    const orderPlacedBlock = panel.querySelector(".order-placed-block");
    const orderFulfilmentBlock = panel.querySelector(".order-fulfilment-block");
    const orderItemsBlock = panel.querySelector(".order-items-block");
    panel.querySelector(".order-name").textContent = orderName || "";
    panel.querySelector(".order-placed-at").textContent = placedAt || "";
    panel.querySelector(".order-fulfilment-status").textContent = fulfilment || "";
    if (orderPanelOrder) orderPanelOrder.hidden = !orderName;
    orderPlacedBlock.hidden = !placedAt;
    orderFulfilmentBlock.hidden = !fulfilment;

    const orderItems = panel.querySelector(".order-items-grid");
    orderItems.innerHTML = "";
    items.slice(0, 4).forEach((entry) => {
      const item = document.createElement("article");
      const quantity = Number(entry.quantity || 1);
      const title = normalizeDisplayText(entry.title);
      const variant = normalizeDisplayText(entry.variant);
      const unitPrice = normalizeDisplayText(entry.unitPrice);
      const imageUrl = normalizeDisplayText(entry.imageUrl);
      const itemUrl = normalizeDisplayText(entry.itemUrl);
      const fullUrl = itemUrl ? new URL(itemUrl, window.location.origin).toString() : "";
      const inner = `
        ${imageUrl ? `<div class="order-item-media"><img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(title)}"></div>` : ""}
        <div class="order-item-copy">
          <div class="order-item-title-row">
            ${fullUrl ? `<a class="order-item-title" href="${escapeHtml(fullUrl)}">${escapeHtml(title)}</a>` : `<strong class="order-item-title">${escapeHtml(title)}</strong>`}
            ${unitPrice ? `<span class="order-item-price">${escapeHtml(unitPrice)}</span>` : ""}
          </div>
          ${variant ? `<div class="order-item-variant">${escapeHtml(variant)}</div>` : ""}
          <div class="order-item-qty">× ${quantity}</div>
        </div>
      `;
      item.className = "order-item-card";
      item.innerHTML = inner;
      orderItems.appendChild(item);
    });
    orderItemsBlock.hidden = !items.length;

    container.hidden = (!orderPanelOrder || orderPanelOrder.hidden)
      && orderPlacedBlock.hidden
      && orderFulfilmentBlock.hidden
      && orderItemsBlock.hidden;
    if (panel) panel.hidden = container.hidden;
  }

  function renderOrderSummary(node, orderSummary) {
    renderOrderSummaryPanel(node.querySelector(".order-summary-panel"), orderSummary);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderShipmentCard(shipment, index, totalShipments) {
    const node = template.content.firstElementChild.cloneNode(true);
    const packageLabel = node.querySelector(".shipment-package-label");
    node.querySelector(".carrier-name").textContent = shipment.carrierName || shipment.carrierCode || "Carrier pending";
    node.querySelector(".tracking-number").textContent = getPrimaryShipmentLabel(shipment);
    node.querySelector(".shipment-summary-line").textContent = getSummarySentence(shipment);
    renderOrderSummary(node, shipment.orderSummary);
    if (packageLabel) {
      packageLabel.hidden = totalShipments <= 1;
      if (totalShipments > 1) {
        packageLabel.textContent = `Package ${index + 1} of ${totalShipments}`;
      }
    }
    if (totalShipments > 1) {
      const orderPanel = node.querySelector(".order-summary-panel");
      if (orderPanel) orderPanel.hidden = true;
    }

    const timeline = node.querySelector(".timeline");
    const timelineToggle = node.querySelector(".timeline-toggle");
    node.querySelector(".destination").textContent = shipment.destinationCountry || "-";
    node.dataset.status = shipment.normalizedStatus || "unknown";
    const lastMileCard = node.querySelector(".last-mile-card");
    const lastMileNumber = normalizeDisplayText(shipment.lastMileTrackingNumber);
    if (lastMileCard) {
      lastMileCard.hidden = !lastMileNumber;
      if (lastMileNumber) {
        node.querySelector(".last-mile-tracking-number").textContent = lastMileNumber;
      }
    }

    const timelineData = collapseOriginEvents(shipment.events || [], shipment);
    const events = timelineData.events;
    node.querySelector(".event-count").textContent = events.length
      ? `${events.length} updates`
      : "No scans yet";
    node.querySelector(".dispatched-at").textContent = formatDate(
      timelineData.dispatchEvent && (timelineData.dispatchEvent.eventTime || timelineData.dispatchEvent.time)
    );
    node.querySelector(".updated-at-secondary").textContent = formatDate(shipment.updatedAt);
    node.querySelector(".status-detail").textContent = normalizeDisplayText(
      formatStatusText(shipment.providerStatus) || formatStatusText(shipment.normalizedStatus) || "-"
    ) || "-";

    const supportNotice = node.querySelector(".support-notice");
    const shouldShowNotice = !events.length || ["exception", "failed_attempt", "unknown", "not_found"].includes(shipment.normalizedStatus);
    const supportText = getSupportNoticeText(shipment, events);
    supportNotice.textContent = supportText;
    supportNotice.hidden = !supportText || (!shouldShowNotice && shipment.normalizedStatus !== "delivered");

    const providerStatus = node.querySelector(".provider-status");
    const providerText = normalizeDisplayText(cleanEventDescription(shipment.providerStatusDescription));
    providerStatus.textContent = providerText;
    providerStatus.hidden = shipment.normalizedStatus === "delivered"
      || !providerText
      || providerText === node.querySelector(".status-detail").textContent;

    const activeProgressIndex = PROGRESS_ORDER.indexOf(shipment.normalizedStatus);
    node.querySelectorAll(".shipment-progress span").forEach((step, progressIndex) => {
      step.classList.toggle("is-active", progressIndex <= activeProgressIndex && activeProgressIndex >= 0);
    });
    if (!events.length) {
      const item = document.createElement("li");
      item.className = "empty-event";
      const emptyTimelineTitle = isCarrierUpdatePending(shipment, events)
        ? "The carrier has not shared any tracking scans yet."
        : "No tracking timeline is available yet.";
      item.innerHTML = `
          <time>-</time>
          <div class="event-text">
            <div class="event-title">${emptyTimelineTitle}</div>
          </div>
        `;
      timeline.appendChild(item);
      timeline.classList.remove("is-collapsed");
      if (timelineToggle) timelineToggle.hidden = true;
    }

    const collapseThreshold = TIMELINE_PINNED_RECENT_COUNT + TIMELINE_PINNED_EARLIEST_COUNT + 1;
    const shouldCollapseTimeline = events.length > collapseThreshold;
    const hiddenStartIndex = TIMELINE_PINNED_RECENT_COUNT;
    const hiddenEndIndex = events.length - TIMELINE_PINNED_EARLIEST_COUNT - 1;
    let hiddenCount = 0;

    events.forEach((event, eventIndex) => {
      const item = document.createElement("li");
      const eventTime = event.eventTime || event.time;
      const eventLocation = formatLocation(event.location, shipment);
      if (shouldCollapseTimeline && eventIndex >= hiddenStartIndex && eventIndex <= hiddenEndIndex) {
        item.dataset.hidden = "true";
        hiddenCount += 1;
      }
      item.innerHTML = `
        <time>${escapeHtml(formatDate(eventTime))}</time>
        <div class="event-text">
          <div class="event-title">${escapeHtml(cleanEventDescription(event.description || ""))}</div>
          ${eventLocation ? `<div class="event-location">${escapeHtml(eventLocation)}</div>` : ""}
        </div>
      `;
      timeline.appendChild(item);
    });

    if (timelineToggle) {
      if (shouldCollapseTimeline && hiddenCount > 0) {
        timeline.classList.add("is-collapsed");
        timelineToggle.hidden = false;
        timelineToggle.textContent = `Show more (${hiddenCount})`;
        timelineToggle.dataset.expanded = "false";
        timelineToggle.onclick = () => {
          const expanded = timelineToggle.dataset.expanded === "true";
          if (expanded) {
            timeline.classList.add("is-collapsed");
            timelineToggle.dataset.expanded = "false";
            timelineToggle.textContent = `Show more (${hiddenCount})`;
          } else {
            timeline.classList.remove("is-collapsed");
            timelineToggle.dataset.expanded = "true";
            timelineToggle.textContent = "Show less";
          }
        };
      } else {
        timeline.classList.remove("is-collapsed");
        timelineToggle.hidden = true;
        timelineToggle.onclick = null;
      }
    }

    return node;
  }

  function getTabStatusLabel(shipment) {
    return formatStatusText(shipment.normalizedStatus || shipment.providerStatus || "unknown");
  }

  function renderShipmentTabs(shipments) {
    if (!shipmentTabs) return;
    shipmentTabs.innerHTML = "";
    shipmentTabs.hidden = shipments.length <= 1;
    if (shipments.length <= 1) return;

    shipments.forEach((shipment, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "shipment-tab-button";
      if (index === activeShipmentIndex) {
        button.classList.add("is-active");
      }
      button.setAttribute("aria-pressed", index === activeShipmentIndex ? "true" : "false");
      button.innerHTML = `
        <span class="shipment-tab-label">Package ${index + 1}</span>
        <span class="shipment-tab-status">${escapeHtml(getTabStatusLabel(shipment))}</span>
      `;
      button.addEventListener("click", () => {
        activeShipmentIndex = index;
        renderShipments(shipments);
      });
      shipmentTabs.appendChild(button);
    });
  }

  function renderShipments(shipments) {
    resultsList.innerHTML = "";
    emptyState.hidden = shipments.length > 0;
    const totalShipments = shipments.length;
    activeShipmentIndex = Math.min(activeShipmentIndex, Math.max(totalShipments - 1, 0));

    if (sharedOrderSummaryPanel) {
      sharedOrderSummaryPanel.hidden = true;
    }
    if (shipmentTabs) {
      shipmentTabs.hidden = true;
      shipmentTabs.innerHTML = "";
    }

    if (!totalShipments) {
      return;
    }

    const sharedSummarySource = shipments.find((shipment) => shipment && shipment.orderSummary && shipment.orderSummary.orderName);
    if (sharedOrderSummaryPanel) {
      if (totalShipments > 1) {
        renderOrderSummaryPanel(sharedOrderSummaryPanel, sharedSummarySource ? sharedSummarySource.orderSummary : null);
      } else {
        sharedOrderSummaryPanel.hidden = true;
      }
    }

    renderShipmentTabs(shipments);
    const activeShipment = shipments[activeShipmentIndex] || shipments[0];
    resultsList.appendChild(renderShipmentCard(activeShipment, activeShipmentIndex, totalShipments));
  }

  async function queryTracking(numbers) {
    const params = new URLSearchParams();
    params.set("nums", numbers.join(","));

    const response = await fetch(`${apiEndpoint}?${params.toString()}`, {
      headers: { Accept: "application/json" }
    });
    if (!response.ok) {
      throw new Error(`Request failed with ${response.status}`);
    }
    return response.json();
  }

  async function queryOrder(orderNumber) {
    const params = new URLSearchParams();
    params.set("order_no", orderNumber);

    const response = await fetch(`${orderApiEndpoint}?${params.toString()}`, {
      headers: { Accept: "application/json" }
    });
    if (!response.ok) {
      throw new Error(`Request failed with ${response.status}`);
    }
    return response.json();
  }

  async function submitQuery() {
    let shouldStartCooldown = false;

    try {
      let data;
      if (queryMode === "order") {
        const orderNumber = (orderNumberInput && orderNumberInput.value || "").trim().toUpperCase();
        if (!orderNumber) {
          setMessage("Please enter your order number.", true);
          trackButton.disabled = false;
          setTrackButtonLabel();
          return;
        }
        trackButton.disabled = true;
        shouldStartCooldown = true;
        setMessage(`Looking up order ${orderNumber}...`);
        data = await queryOrder(orderNumber);
      } else {
        const rawInput = (textarea.value || "").trim();
        if (looksLikeOrderNumber(rawInput) && !rawInput.includes(" ")) {
          setMessage("That looks like an order number. Switch to Order Number to search by order.", true);
          setQueryMode("order");
          if (orderNumberInput) orderNumberInput.value = rawInput.toUpperCase();
          if (orderNumberInput) orderNumberInput.focus();
          trackButton.disabled = false;
          setTrackButtonLabel();
          return;
        }
        const numbers = parseTrackingNumbers(textarea.value);
        if (!numbers.length) {
          setMessage("Please enter a valid tracking number.", true);
          trackButton.disabled = false;
          setTrackButtonLabel();
          return;
        }
        trackButton.disabled = true;
        shouldStartCooldown = true;
        setMessage(`Tracking ${numbers.length} shipment${numbers.length > 1 ? "s" : ""}...`);
        data = await queryTracking(numbers);
      }

      renderShipments(data.shipments || []);
      if (data.success === false && !(data.shipments || []).length) {
        setMessage((data.errors && data.errors[0] && data.errors[0].message) || "No shipment data was returned.", true);
      } else if (data.errors && data.errors.length) {
        setMessage(data.errors[0].message || "Some shipments failed to load.", true);
      } else {
        setMessage(`Loaded ${data.shipments.length} shipment${data.shipments.length > 1 ? "s" : ""}.`);
      }
    } catch (error) {
      setMessage("Tracking lookup failed. Please try again later.", true);
      emptyState.hidden = false;
      resultsList.innerHTML = "";
      console.error(error);
    } finally {
      if (shouldStartCooldown) {
        startCooldown();
      }
    }
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      submitQuery();
    });
  }

  modeButtons.forEach((button) => {
    button.addEventListener("click", function () {
      setQueryMode(button.dataset.mode);
      setMessage("");
    });
  });

  const url = new URL(window.location.href);
  const nums = url.searchParams.get("nums");
  const orderNo = url.searchParams.get("order_no");
  setQueryMode("tracking");
  if (nums && textarea) {
    textarea.value = nums;
    submitQuery();
  } else if (orderNo && orderNumberInput) {
    setQueryMode("order");
    orderNumberInput.value = orderNo;
    submitQuery();
  }

  setTrackButtonLabel();
})();
