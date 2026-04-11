const homeGallery = document.getElementById("home-gallery");
const projectList = document.getElementById("project-list");
const productList = document.getElementById("product-list");
const slidesContainer = document.getElementById("slides");
const dotsContainer = document.getElementById("dots");
const prevButton = document.getElementById("prev-slide");
const nextButton = document.getElementById("next-slide");
const form = document.getElementById("contact-form");
const formStatus = document.getElementById("form-status");

let currentSlide = 0;
let slideTimer = null;
let slideElements = [];
let dotElements = [];

function getDataUrl() {
  const scriptEl = document.querySelector('script[src$="app.js"]');
  if (!scriptEl) {
    return "./assets/data/data.json";
  }

  return new URL("../data/data.json", scriptEl.src).href;
}

function renderHomePhotos(products) {
  if (!homeGallery) return;

  homeGallery.innerHTML = products
    .map(
      (product) => `
        <li>
          <img src="${product.image}" alt="${product.name}" loading="lazy" />
        </li>
      `
    )
    .join("");
}

function renderProjects(projects) {
  if (!projectList) return;

  projectList.innerHTML = projects
    .map(
      (project) => `
        <article>
          <h3>${project.title}</h3>
          <p>${project.description}</p>
          <ul class="simple-list">
            ${project.points.map((point) => `<li>${point}</li>`).join("")}
          </ul>
        </article>
      `
    )
    .join("");
}

function renderProducts(products) {
  if (!productList) return;

  productList.innerHTML = products
    .map(
      (product) => `
        <article class="product-row">
          <img src="${product.image}" alt="${product.name}" loading="lazy" />
          <div>
            <h3>${product.name}</h3>
            <p>${product.description}</p>
            <p class="price">${product.price}</p>
          </div>
        </article>
      `
    )
    .join("");
}

function setActiveSlide(index) {
  if (!slideElements.length) return;

  currentSlide = (index + slideElements.length) % slideElements.length;

  slideElements.forEach((slide, i) => {
    slide.classList.toggle("is-active", i === currentSlide);
  });

  dotElements.forEach((dot, i) => {
    dot.classList.toggle("is-active", i === currentSlide);
  });
}

function restartAutoSlide() {
  clearInterval(slideTimer);
  slideTimer = setInterval(() => setActiveSlide(currentSlide + 1), 4000);
}

function renderSlides(slides) {
  if (!slidesContainer || !dotsContainer) return;

  slidesContainer.innerHTML = slides
    .map(
      (slide) => `
        <article class="slide">
          <h3>${slide.title}</h3>
          <p>${slide.text}</p>
        </article>
      `
    )
    .join("");

  dotsContainer.innerHTML = slides
    .map((_, index) => `<button class="dot" type="button" data-index="${index}" aria-label="${index + 1}番目のスライド"></button>`)
    .join("");

  slideElements = [...slidesContainer.querySelectorAll(".slide")];
  dotElements = [...dotsContainer.querySelectorAll(".dot")];

  dotElements.forEach((dot) => {
    dot.addEventListener("click", () => {
      setActiveSlide(Number(dot.dataset.index));
      restartAutoSlide();
    });
  });

  setActiveSlide(0);
  restartAutoSlide();
}

async function loadContent() {
  try {
    const dataUrl = getDataUrl();
    const response = await fetch(dataUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();
    const products = data.products.map((product) => ({
      ...product,
      image: new URL(product.image, response.url).href
    }));

    renderHomePhotos(products);
    renderProjects(data.projects);
    renderProducts(products);
    renderSlides(data.slides);
  } catch (error) {
    const message = `情報の取得に失敗しました: ${error.message}`;

    if (homeGallery) homeGallery.innerHTML = `<li class="status">${message}</li>`;
    if (projectList) projectList.innerHTML = `<p class="status">${message}</p>`;
    if (productList) productList.innerHTML = `<p class="status">${message}</p>`;
    if (slidesContainer) slidesContainer.innerHTML = `<p class="status">${message}</p>`;
    if (dotsContainer) dotsContainer.innerHTML = "";
  }
}

async function submitContact(event) {
  event.preventDefault();
  if (!form || !formStatus) return;

  const formData = new FormData(form);
  const message = formData.get("message");
  formStatus.textContent = "送信中です...";

  try {
    const response = await fetch(getDataUrl(), {
      method: "GET",
      headers: {
        "X-Contact-Message": String(message)
      }
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    await response.json();
    formStatus.textContent = "送信を受け付けました。ご連絡ありがとうございます。";
    form.reset();
  } catch (error) {
    formStatus.textContent = `送信に失敗しました: ${error.message}`;
  }
}

if (prevButton) {
  prevButton.addEventListener("click", () => {
    setActiveSlide(currentSlide - 1);
    restartAutoSlide();
  });
}

if (nextButton) {
  nextButton.addEventListener("click", () => {
    setActiveSlide(currentSlide + 1);
    restartAutoSlide();
  });
}

if (form) {
  form.addEventListener("submit", submitContact);
}

loadContent();
