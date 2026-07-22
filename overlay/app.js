const cfg = window.OVERLAY_CONFIG;

const card = document.getElementById("ad-card");
const imageEl = document.getElementById("ad-image");
const nameEl = document.getElementById("ad-name");
const priceEl = document.getElementById("ad-price");
const ctaEl = document.getElementById("ad-cta");

let hideTimer = null;

function connect() {
  const ws = new WebSocket(cfg.websocketUrl);

  ws.addEventListener("open", () => console.log("[overlay] connected to detector"));
  ws.addEventListener("close", () => {
    console.log("[overlay] disconnected, retrying in 3s");
    setTimeout(connect, 3000);
  });
  ws.addEventListener("error", () => ws.close());

  ws.addEventListener("message", async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    if (msg.event === "merch_detected" && msg.product) {
      showAd(msg.product);
    }
  });
}

async function fetchProduct(handle) {
  const url = `https://storefront-api.fourthwall.com/v1/products/${encodeURIComponent(handle)}?storefront_token=${encodeURIComponent(cfg.storefrontToken)}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Fourthwall API returned ${res.status}`);
  }
  const data = await res.json();
  console.log("[overlay] product data", data); // inspect this if fields below look wrong for your store
  return data;
}

function pickImage(data) {
  if (Array.isArray(data.images) && data.images.length > 0) {
    return data.images[0].url;
  }
  return "";
}

function pickPrice(data) {
  const variant = Array.isArray(data.variants) ? data.variants[0] : null;
  const price = variant && variant.unitPrice;
  if (price && price.value != null) {
    return `${price.value} ${price.currency}`;
  }
  return "";
}

function productUrl(data, handle) {
  const slug = data.slug || handle;
  return `https://${cfg.shopDomain}.fourthwall.com/products/${slug}`;
}

async function showAd(handle) {
  try {
    const data = await fetchProduct(handle);
    imageEl.src = pickImage(data);
    nameEl.textContent = data.name || handle;
    priceEl.textContent = pickPrice(data);
    ctaEl.textContent = `Get it: ${cfg.shopDomain}.fourthwall.com`;
    ctaEl.href = productUrl(data, handle);
  } catch (err) {
    console.error("[overlay] failed to load product", handle, err);
    nameEl.textContent = handle;
    priceEl.textContent = "";
    imageEl.src = "";
    ctaEl.textContent = `${cfg.shopDomain}.fourthwall.com`;
    ctaEl.href = `https://${cfg.shopDomain}.fourthwall.com`;
  }

  card.classList.remove("hidden");
  requestAnimationFrame(() => card.classList.add("visible"));

  if (hideTimer) clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    card.classList.remove("visible");
    setTimeout(() => card.classList.add("hidden"), 400);
  }, (cfg.displaySeconds || 8) * 1000);
}

connect();
