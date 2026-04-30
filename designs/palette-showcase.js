const paletteTabs = [...document.querySelectorAll(".palette-tabs button")];
const palettePages = [...document.querySelectorAll(".palette-page")];

function showPalettePage(id) {
  paletteTabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.target === id));
  palettePages.forEach((page) => page.classList.toggle("is-active", page.id === id));
  if (location.hash !== `#${id}`) {
    history.replaceState(null, "", `#${id}`);
  }
}

function getTrackingNumber(value) {
  const match = value.match(/[a-z0-9]{6,42}/i);
  return match ? match[0].toUpperCase() : "RJ556381428CN";
}

paletteTabs.forEach((tab) => {
  tab.addEventListener("click", () => showPalettePage(tab.dataset.target));
});

document.querySelectorAll(".concept-form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const target = form.closest(".palette-page").querySelector("[data-number]");
    if (target) target.textContent = getTrackingNumber(form.elements.tracking.value);
  });
});

const initialPalette = location.hash.replace("#", "");
if (palettePages.some((page) => page.id === initialPalette)) {
  showPalettePage(initialPalette);
}
