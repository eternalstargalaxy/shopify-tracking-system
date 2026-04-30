const tabs = [...document.querySelectorAll(".concept-tabs button")];
const pages = [...document.querySelectorAll(".concept-page")];

function showConcept(id) {
  tabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.target === id));
  pages.forEach((page) => page.classList.toggle("is-active", page.id === id));
  if (location.hash !== `#${id}`) {
    history.replaceState(null, "", `#${id}`);
  }
}

function firstTrackingNumber(value) {
  const match = value.match(/[a-z0-9]{6,42}/i);
  return match ? match[0].toUpperCase() : "RJ556381428CN";
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => showConcept(tab.dataset.target));
});

document.querySelectorAll(".concept-form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const field = form.elements.tracking;
    const number = firstTrackingNumber(field.value);
    const page = form.closest(".concept-page");
    const target = page.querySelector("[data-number]");
    if (target) target.textContent = number;
  });
});

const initial = location.hash.replace("#", "");
if (pages.some((page) => page.id === initial)) {
  showConcept(initial);
}
