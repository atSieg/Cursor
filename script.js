const dialog = document.getElementById("product-dialog");
const dialogTitle = document.getElementById("dialog-title");
const dialogPrice = document.getElementById("dialog-price");
const form = document.getElementById("booking-form");
const formNote = document.getElementById("form-note");
const modelSelect = form?.querySelector('select[name="model"]');

document.querySelectorAll(".product").forEach((product) => {
  const hit = product.querySelector(".product-hit");
  hit?.addEventListener("click", () => {
    dialogTitle.textContent = product.dataset.name ?? "";
    dialogPrice.textContent = product.dataset.price ?? "";
    if (modelSelect && product.dataset.name) {
      modelSelect.value = product.dataset.name;
    }
    dialog?.showModal();
  });
});

dialog?.querySelector('a[href="#contact"]')?.addEventListener("click", () => {
  dialog.close();
});

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  formNote.hidden = false;
  form.reset();
});

const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-in");
        observer.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.25 }
);

document.querySelectorAll(".craft-text, .craft-visual").forEach((el) => {
  observer.observe(el);
});
