const WEIGHT_KEYS = ["price", "seller", "reviews", "warranty", "delivery"];

const COUNTRIES = [
  ["TR", "Türkiye", "TRY"],
  ["DE", "Germany", "EUR"],
  ["GB", "United Kingdom", "GBP"],
  ["US", "United States", "USD"],
  ["JP", "Japan", "JPY"],
  ["FR", "France", "EUR"],
  ["IT", "Italy", "EUR"],
  ["ES", "Spain", "EUR"],
  ["NL", "Netherlands", "EUR"],
  ["AU", "Australia", "AUD"],
  ["CA", "Canada", "CAD"],
  ["KR", "South Korea", "KRW"],
  ["AE", "United Arab Emirates", "AED"],
  ["IN", "India", "INR"],
  ["CH", "Switzerland", "CHF"],
  ["SE", "Sweden", "SEK"],
  ["PL", "Poland", "PLN"],
];

const CURRENCIES = [...new Set(COUNTRIES.map((row) => row[2]))];

const GEO_BOXES = [
  { country: "TR", currency: "TRY", lat: [36, 42.4], lng: [26, 45] },
  { country: "DE", currency: "EUR", lat: [47.2, 55.1], lng: [5.8, 15.1] },
  { country: "GB", currency: "GBP", lat: [49.8, 58.7], lng: [-8.2, 1.8] },
  { country: "US", currency: "USD", lat: [24.5, 49.4], lng: [-125, -66.9] },
  { country: "JP", currency: "JPY", lat: [30.2, 45.6], lng: [129, 146] },
  { country: "FR", currency: "EUR", lat: [42.3, 51.1], lng: [-5.2, 8.3] },
];

const HIGHLIGHT_LABELS = {
  best_overall: "best for you",
  lowest_list_price: "best price",
  lowest_total_cost: "lowest landed",
  best_seller: "most rated",
  best_warranty: "best warranty",
};

const PROPERTY_LABELS = {
  storage_gb: "storage",
  memory_gb: "memory",
  colour: "colour",
  color: "colour",
  family_id: "product",
  variant_id: "build",
  region_version: "region",
  processor: "processor",
  connectivity: "connectivity",
};

const FUN_FACTS = [
  "A 512 GB phone is not the same product as a 1 TB one — we keep those listings apart.",
  "Sticker price is not landed cost. Shipping, VAT and registration can flip the ranking.",
  "Weights are proportions. Pushing every slider to the top ranks the same as leaving them low.",
  "If two winning lenses name the same offer, they collapse into one card instead of repeating it.",
  "Close alternatives only appear when a different spec or a comparable product actually exists.",
];

const state = {
  origin: "default",
  manualGeo: false,
  session: null,
  catalog: { families: [], variants: [] },
  factTimer: null,
  busy: false,
};

function $(id) {
  return document.getElementById(id);
}

export function collapseHighlights(highlights) {
  const groups = [];
  const indexByOffer = new Map();
  for (const highlight of highlights || []) {
    const existing = indexByOffer.get(highlight.offer_id);
    if (existing === undefined) {
      indexByOffer.set(highlight.offer_id, groups.length);
      groups.push([highlight]);
    } else {
      groups[existing].push(highlight);
    }
  }
  return groups.slice(0, 5);
}

function uniqueCountries() {
  const seen = new Set();
  return COUNTRIES.filter(([code]) => {
    if (seen.has(code)) return false;
    seen.add(code);
    return true;
  });
}

function fillSelects() {
  const country = $("country");
  const currency = $("currency");
  country.innerHTML = "";
  currency.innerHTML = "";
  for (const [code, name] of uniqueCountries()) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = name;
    country.append(option);
  }
  for (const code of CURRENCIES) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = code;
    currency.append(option);
  }
  country.value = "TR";
  currency.value = "TRY";
}

function currentPreferences() {
  const weights = {};
  for (const key of WEIGHT_KEYS) {
    const input = document.querySelector(`[data-weight="${key}"]`);
    weights[key] = Number(input.value) / 100;
  }
  return {
    destination_country: $("country").value,
    reference_currency: $("currency").value,
    origin: state.origin,
    weights,
    include_used: Boolean($("include-used") && $("include-used").checked),
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg || JSON.stringify(item)).join("; ")
          : response.statusText;
    throw new Error(message);
  }
  return body;
}

function showView(name) {
  for (const view of document.querySelectorAll(".view")) {
    view.hidden = view.dataset.view !== name;
  }
  window.scrollTo(0, 0);
}

function setError(id, message) {
  const node = $(id);
  node.hidden = !message;
  node.textContent = message || "";
}

function money(value) {
  if (value == null || value.amount == null) return "—";
  const amount = Number(value.amount);
  const currency = value.currency || "";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "USD",
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`.trim();
  }
}

function timeAgo(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  const delta = Math.max(0, Date.now() - then.getTime());
  const minutes = Math.round(delta / 60000);
  if (minutes < 1) return "price seen just now";
  if (minutes < 60) return `price seen ${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `price seen ${hours} h ago`;
  return `price seen ${then.toLocaleDateString()}`;
}

function placeholderLabel(title) {
  return (title || "item")
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0] || "")
    .join("")
    .toUpperCase();
}

function pictureNode(offer) {
  const wrap = document.createElement("div");
  wrap.className = "picture";
  if (offer?.image_url) {
    const img = document.createElement("img");
    img.src = offer.image_url;
    img.alt = offer.listing_title || "Product";
    wrap.append(img);
    return wrap;
  }
  const mark = document.createElement("span");
  mark.textContent = placeholderLabel(offer?.listing_title);
  wrap.append(mark);
  return wrap;
}

function appendMeta(parent, text) {
  if (!text) return;
  const line = document.createElement("p");
  line.className = "meta";
  line.textContent = text;
  parent.append(line);
}

function costLines(offer) {
  const landed = offer.landed_cost;
  if (!landed) return [];
  const rows = [];
  const push = (line) => {
    if (!line) return;
    const tag = line.origin === "estimated" ? "estimated" : line.origin === "quoted" ? "quoted" : "";
    rows.push(`${line.label || "fee"}: ${money(line.amount)}${tag ? ` (${tag})` : ""}`);
  };
  push(landed.shipping);
  push(landed.taxes);
  push(landed.import_duties);
  push(landed.registration_fees);
  for (const extra of landed.other_fees || []) push(extra);
  return rows;
}

function explanationBlock(explanation, heading) {
  const wrap = document.createElement("div");
  wrap.className = "why";
  if (heading) {
    const title = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = heading;
    title.append(strong);
    wrap.append(title);
  }
  if (explanation?.headline) {
    const headline = document.createElement("p");
    headline.textContent = explanation.headline;
    wrap.append(headline);
  }
  for (const reason of explanation?.reasons || []) {
    const line = document.createElement("p");
    line.textContent = reason.detail;
    wrap.append(line);
  }
  for (const caveat of explanation?.caveats || []) {
    const line = document.createElement("p");
    line.textContent = caveat;
    wrap.append(line);
  }
  return wrap;
}


function conditionLabel(condition) {
  switch (condition) {
    case "new":
      return "New";
    case "used":
      return "Used / second-hand";
    case "refurbished":
      return "Refurbished";
    case "open_box":
      return "Open box";
    case "unknown":
      return "Condition not stated";
    default:
      return condition ? String(condition) : "";
  }
}

function fillOfferEconomics(card, offer) {
  const price = document.createElement("p");
  price.className = "price-line";
  const strong = document.createElement("strong");
  const converted = offer.converted_list_price?.reference;
  strong.textContent = money(converted || offer.list_price);
  price.append(strong);
  if (
    offer.list_price &&
    converted &&
    offer.list_price.currency !== converted.currency
  ) {
    const og = document.createElement("span");
    og.className = "og-currency";
    og.textContent = ` — ${money(offer.list_price)}`;
    price.append(og);
  }
  card.append(price);

  if (offer.landed_cost) {
    appendMeta(card, `landing cost ${money(offer.landed_cost.total)}`);
  }
  for (const line of costLines(offer)) appendMeta(card, line);
  if (offer.warranty) appendMeta(card, `warranty ${offer.warranty}`);
  if (offer.condition && offer.condition !== "new") {
    const label = conditionLabel(offer.condition);
    if (label) appendMeta(card, label);
  } else if (offer.condition === "new") {
    appendMeta(card, "New");
  }
  const seller = offer.seller || {};
  if (seller.reliability != null) {
    appendMeta(card, `trust score ${(Number(seller.reliability) * 100).toFixed(0)}% — ${seller.name || offer.source_id}`);
  } else if (seller.name) {
    appendMeta(card, `seller ${seller.name}`);
  }
  if (offer.country) appendMeta(card, `country ${offer.country}`);
  if (seller.review_count != null) {
    const ratings = document.createElement("p");
    ratings.className = "meta";
    const link = document.createElement("a");
    link.className = "retailer";
    link.href = offer.listing_url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `${seller.review_count} ratings`;
    ratings.append(link);
    card.append(ratings);
  }
  const fresh = document.createElement("p");
  fresh.className = "fresh";
  fresh.textContent = timeAgo(offer.collected_at);
  card.append(fresh);
}

function originalSearchName(page) {
  const variant = page.confirmed_variant;
  const query = (state.session?.raw_query || "").trim();
  if (variant?.model_name) {
    const bits = [variant.model_name];
    if (variant.storage_gb != null) bits.push(`${variant.storage_gb} GB`);
    if (variant.memory_gb != null) bits.push(`${variant.memory_gb} GB RAM`);
    if (variant.colour) bits.push(variant.colour);
    return bits.join(" · ");
  }
  return query;
}

function renderHighlights(page, offersById) {
  const row = $("highlight-row");
  row.innerHTML = "";
  const groups = collapseHighlights(page.highlights);
  row.style.setProperty("--count", String(Math.max(groups.length, 1)));
  row.dataset.count = String(groups.length);
  const searched = originalSearchName(page);
  for (const group of groups) {
    const offer = offersById.get(group[0].offer_id);
    if (!offer) continue;
    const card = document.createElement("article");
    card.className = "offer-card";
    const title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = group.map((item) => HIGHLIGHT_LABELS[item.kind] || item.kind).join(" · ");
    card.append(title);
    if (searched) {
      const product = document.createElement("p");
      product.className = "searched-name";
      product.textContent = searched;
      card.append(product);
    }
    card.append(pictureNode(offer));
    fillOfferEconomics(card, offer);
    const best = group.find((item) => item.kind === "best_overall") || group[0];
    const whyLabel = group.some((item) => item.kind === "best_overall")
      ? "why this is best for you"
      : "why this highlight";
    card.append(explanationBlock(best.explanation, whyLabel));
    row.append(card);
  }
}

function renderFullList(page, offersById, highlightedIds) {
  const list = $("full-list");
  list.hidden = true;
  list.innerHTML = "";
  for (const offer of page.offers || []) {
    const item = document.createElement("article");
    item.className = "full-item";
    const name = document.createElement("div");
    const heading = document.createElement("strong");
    heading.textContent = offer.listing_title;
    name.append(heading);
    if (highlightedIds.has(offer.id)) {
      const note = document.createElement("div");
      note.textContent = "already on a highlight card";
      name.append(note);
    }
    const price = document.createElement("div");
    price.textContent = money(offer.landed_cost?.total || offer.converted_list_price?.reference);
    const seller = document.createElement("div");
    seller.textContent = offer.seller?.name || offer.country;
    item.append(name, price, seller);
    list.append(item);
  }
  void offersById;
}

function specLabel(key) {
  return String(key)
    .replace(/_gb$/, "")
    .replace(/_inch$/, "")
    .replace(/_mah$/, "")
    .replaceAll("_", " ");
}

function specUnit(key, given) {
  if (given) return given;
  return { storage_gb: "GB", memory_gb: "GB RAM", display_inch: "in", battery_mah: "mAh" }[key] || "";
}

function specLines(offer) {
  const lines = [];
  for (const spec of offer.raw_specs || []) {
    const unit = specUnit(spec.key, spec.unit);
    lines.push(`${specLabel(spec.key)}: ${spec.value}${unit ? ` ${unit}` : ""}`);
  }
  if (!lines.length) {
    if (offer.model_number) lines.push(`model: ${offer.model_number}`);
    if (offer.gtin) lines.push(`gtin: ${offer.gtin}`);
  }
  return lines.slice(0, 6);
}

function renderAlternatives(page, altById) {
  const section = $("alternatives");
  const row = $("alternative-row");
  const cue = $("scroll-cue");
  row.innerHTML = "";
  const alts = page.alternatives || [];
  if (!alts.length) {
    section.hidden = true;
    cue.hidden = true;
    return;
  }
  section.hidden = false;
  cue.hidden = false;
  row.style.setProperty("--count", String(alts.length));
  alts.forEach((alt, index) => {
    const offer = altById.get(alt.offer_id);
    if (!offer) return;
    const card = document.createElement("article");
    card.className = "alt-card";
    const kicker = document.createElement("p");
    kicker.className = "alt-kicker";
    kicker.style.gridColumn = "1 / -1";
    kicker.textContent = `alternative option ${index + 1}`;
    if (alt.badge) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = alt.badge;
      kicker.append(badge);
    }
    card.append(kicker);
    card.append(pictureNode(offer));
    const copy = document.createElement("div");
    const name = document.createElement("p");
    name.innerHTML = "";
    const strong = document.createElement("strong");
    strong.textContent = offer.listing_title;
    name.append(strong);
    copy.append(name);
    appendMeta(copy, money(offer.landed_cost?.total || offer.converted_list_price?.reference));
    const specHead = document.createElement("p");
    specHead.className = "meta";
    specHead.textContent = "specs:";
    copy.append(specHead);
    for (const line of specLines(offer)) appendMeta(copy, `— ${line}`);
    for (const reason of alt.explanation?.reasons || []) {
      if (reason.factor === "cost" || reason.factor === "value") continue;
      appendMeta(copy, `— ${reason.detail}`);
    }
    card.append(copy);
    const why = explanationBlock(alt.explanation, "why this is recommended as an alternative");
    why.classList.add("alt-why");
    card.append(why);
    row.append(card);
  });
}

function startLoadingFacts() {
  const node = $("loading-fact");
  let index = 0;
  node.textContent = FUN_FACTS[0];
  clearInterval(state.factTimer);
  state.factTimer = setInterval(() => {
    index = (index + 1) % FUN_FACTS.length;
    node.textContent = FUN_FACTS[index];
  }, 2800);
}

function stopLoadingFacts() {
  clearInterval(state.factTimer);
  state.factTimer = null;
}

async function ensureCatalog() {
  if (state.catalog.families.length) return;
  const [families, variants] = await Promise.all([
    api("/api/catalog/families"),
    api("/api/catalog/variants"),
  ]);
  state.catalog.families = families;
  state.catalog.variants = variants;
}

function optionLabel(property, value) {
  if (property === "family_id") {
    const family = state.catalog.families.find((item) => item.id === value);
    if (family) return `${family.brand} ${family.family_name}`;
  }
  if (property === "variant_id") {
    const variant = state.catalog.variants.find((item) => item.id === value);
    if (variant) {
      const bits = [variant.model_name, variant.storage_gb && `${variant.storage_gb} GB`, variant.colour];
      return bits.filter(Boolean).join(" · ");
    }
  }
  if (property === "storage_gb" || property === "memory_gb") return `${value} GB`;
  return String(value);
}

function reasonCopy(prompt) {
  const label = PROPERTY_LABELS[prompt.property_key] || prompt.property_key.replaceAll("_", " ");
  switch (prompt.reason) {
    case "missing":
      return `Which ${label} should we search for?`;
    case "invalid":
      return `That ${label} is not a valid option. Pick one of these:`;
    case "ambiguous":
      return `More than one ${label} could match. Choose one:`;
    case "shorthand":
      return "Which product did you mean?";
    case "no_match":
      return "Nothing in the catalogue is close enough to search for.";
    case "no_exact_variant":
      return "That exact build is not stocked. Closest options:";
    default:
      return `Confirm the ${label}.`;
  }
}

async function openConfirm(session) {
  await ensureCatalog();
  const prompts = session.normalized_query?.pending_properties || [];
  const box = $("confirm-prompts");
  box.innerHTML = "";
  setError("confirm-error", "");
  const canContinue = prompts.some((prompt) => prompt.options?.length);
  $("confirm-continue").disabled = !canContinue && prompts.some((p) => p.role === "identity");
  for (const prompt of prompts) {
    const block = document.createElement("div");
    block.className = "prompt-block";
    const text = document.createElement("p");
    text.textContent = reasonCopy(prompt);
    block.append(text);
    const options = document.createElement("div");
    options.className = "options";
    for (const value of prompt.options || []) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = prompt.allow_not_important ? "radio" : "radio";
      input.name = prompt.property_key;
      input.value = String(value);
      input.required = prompt.role === "identity";
      input.dataset.kind = "value";
      input.dataset.raw = JSON.stringify(value);
      label.append(input, document.createTextNode(optionLabel(prompt.property_key, value)));
      options.append(label);
    }
    if (prompt.allow_not_important) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = prompt.property_key;
      input.value = "";
      input.dataset.kind = "not_important";
      label.append(input, document.createTextNode("not important"));
      options.append(label);
    }
    block.append(options);
    box.append(block);
  }
  $("confirm-dialog").showModal();
}

function readChoices(session) {
  const prompts = session.normalized_query?.pending_properties || [];
  const choices = [];
  for (const prompt of prompts) {
    const selected = document.querySelector(`input[name="${CSS.escape(prompt.property_key)}"]:checked`);
    if (!selected) {
      if (prompt.role === "identity") {
        throw new Error(`Pick a ${PROPERTY_LABELS[prompt.property_key] || prompt.property_key}.`);
      }
      continue;
    }
    if (selected.dataset.kind === "not_important") {
      choices.push({ property_key: prompt.property_key, kind: "not_important", value: null });
    } else {
      choices.push({
        property_key: prompt.property_key,
        kind: "value",
        value: JSON.parse(selected.dataset.raw),
      });
    }
  }
  return choices;
}

function renderDecision(page) {
  const offersById = new Map((page.offers || []).map((offer) => [offer.id, offer]));
  const altById = new Map((page.alternative_offers || []).map((offer) => [offer.id, offer]));
  const variant = page.confirmed_variant;
  $("decision-kicker").textContent = variant
    ? `${variant.model_name}${variant.storage_gb ? ` · ${variant.storage_gb} GB` : ""}`
    : "Decision";
  const groups = collapseHighlights(page.highlights);
  const highlightedIds = new Set(groups.map((group) => group[0].offer_id));
  renderHighlights(page, offersById);
  renderFullList(page, offersById, highlightedIds);
  renderAlternatives(page, altById);
  $("full-list").hidden = true;
  $("toggle-full-list").setAttribute("aria-expanded", "false");
  showView("decision");
  const cue = $("scroll-cue");
  cue.classList.remove("is-large");
}

async function runSession(session) {
  showView("loading");
  startLoadingFacts();
  try {
    const page = await api("/api/search/run", {
      method: "POST",
      body: JSON.stringify(session),
    });
    stopLoadingFacts();
    renderDecision(page);
  } catch (error) {
    stopLoadingFacts();
    showView("search");
    setError("search-error", error.message);
  }
}

async function startSearch(event) {
  event.preventDefault();
  if (state.busy) return;
  setError("search-error", "");
  const query = $("query").value.trim();
  if (!query) return;
  state.busy = true;
  try {
    const session = await api("/api/search/start", {
      method: "POST",
      body: JSON.stringify({ query, preferences: currentPreferences() }),
    });
    state.session = session;
    if (session.status === "needs_confirmation") {
      await openConfirm(session);
      return;
    }
    await runSession(session);
  } catch (error) {
    setError("search-error", error.message);
  } finally {
    state.busy = false;
  }
}

function bindUi() {
  fillSelects();
  $("search-form").addEventListener("submit", startSearch);
  $("logo-link").addEventListener("click", (event) => {
    event.preventDefault();
    stopLoadingFacts();
    showView("search");
    $("query").focus();
  });
  $("toggle-weights").addEventListener("click", () => {
    const panel = $("panel-weights");
    panel.hidden = !panel.hidden;
    $("toggle-weights").setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("toggle-geo").addEventListener("click", () => {
    const panel = $("panel-geo");
    panel.hidden = !panel.hidden;
    $("toggle-geo").setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("geo-info").addEventListener("click", () => {
    $("geo-tip").hidden = !$("geo-tip").hidden;
  });
  $("country").addEventListener("change", () => {
    state.manualGeo = true;
    state.origin = "manual";
    const match = uniqueCountries().find(([code]) => code === $("country").value);
    if (match) $("currency").value = match[2];
  });
  $("currency").addEventListener("change", () => {
    state.manualGeo = true;
    state.origin = "manual";
  });
  for (const input of document.querySelectorAll("[data-weight]")) {
    input.addEventListener("input", () => {
      document.querySelector(`[data-weight-value="${input.dataset.weight}"]`).textContent = input.value;
    });
  }
  $("confirm-cancel").addEventListener("click", () => $("confirm-dialog").close());
  $("confirm-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.session || state.busy) return;
    state.busy = true;
    try {
      const choices = readChoices(state.session);
      const session = await api("/api/search/confirm", {
        method: "POST",
        body: JSON.stringify({ session: state.session, choices }),
      });
      state.session = session;
      $("confirm-dialog").close();
      await runSession(session);
    } catch (error) {
      setError("confirm-error", error.message);
    } finally {
      state.busy = false;
    }
  });
  $("toggle-full-list").addEventListener("click", () => {
    const list = $("full-list");
    list.hidden = !list.hidden;
    $("toggle-full-list").setAttribute("aria-expanded", String(!list.hidden));
  });
  const cue = $("scroll-cue");
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        cue.classList.toggle("is-large", entry.isIntersecting);
      }
    },
    { threshold: 0.35 },
  );
  observer.observe($("alternatives"));

  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (state.manualGeo) return;
        const { latitude, longitude } = position.coords;
        const hit = GEO_BOXES.find(
          (box) =>
            latitude >= box.lat[0] &&
            latitude <= box.lat[1] &&
            longitude >= box.lng[0] &&
            longitude <= box.lng[1],
        );
        if (!hit) return;
        $("country").value = hit.country;
        $("currency").value = hit.currency;
        state.origin = "geolocation";
      },
      () => {},
      { maximumAge: 600000, timeout: 2500 },
    );
  }
}

bindUi();
