// EuroMillions Analyzer: what every page shares. Every figure comes from the server;
// this file only fetches, formats, translates and lays out.
"use strict";

const EM = (() => {
  const MINUS = "−";
  const NBSP = " ";
  const NNBSP = " "; // French narrow no-break space, before % ; : ! ?

  // --- Language: English or French, remembered, the browser's language at first ---

  const LANGUAGES = ["en", "fr"];
  const STORAGE_KEY = "euromillions-language";

  function detectLanguage() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (LANGUAGES.includes(saved)) return saved;
    } catch (error) {
      // Storage may be unavailable: fall back to the browser's language.
    }
    return (navigator.language || "").toLowerCase().startsWith("fr") ? "fr" : "en";
  }

  let lang = detectLanguage();

  // ``tr(english, french)``: the text in the current language.
  const tr = (en, fr) => (lang === "fr" ? fr : en);
  const language = () => lang;
  const locale = () => (lang === "fr" ? "fr-FR" : "en-GB");

  const PAGES = [
    ["index.html", ["Numbers", "Numéros"]],
    ["patterns.html", ["Patterns", "Répartitions"]],
    ["suggest.html", ["Suggest", "Suggestions"]],
    ["backtest.html", ["Backtest", "Backtest"]],
  ];

  const STRATEGIES = {
    hot: ["hot", "chauds", "Favour numbers drawn more often than expected in recent draws",
      "Privilégie les numéros sortis plus souvent que prévu ces derniers tirages"],
    cold: ["cold", "en retard", "Favour overdue numbers, those not drawn for the longest time",
      "Privilégie les numéros en retard, absents depuis le plus longtemps"],
    balanced: ["balanced", "équilibrée", "Hot and overdue numbers, with the usual odd/even and low/high mix",
      "Numéros chauds et en retard, avec les répartitions pair/impair et bas/haut habituelles"],
    weighted: ["weighted", "pondérée", "Weight numbers by frequency, overdue gap and pair affinity",
      "Pondère les numéros selon leur fréquence, leur retard et leurs affinités par paires"],
    anti: ["anti", "anti-motifs", "Uniformly random lines that avoid historically rare patterns",
      "Grilles tirées au hasard qui évitent les profils rares par le passé"],
    random: ["random (baseline)", "aléatoire (référence)", "Uniformly random lines", "Grilles tirées au hasard"],
  };

  const strategyName = (name) => (STRATEGIES[name] ? tr(STRATEGIES[name][0], STRATEGIES[name][1]) : name);
  const strategyDescription = (name, fallback) =>
    (STRATEGIES[name] ? tr(STRATEGIES[name][2], STRATEGIES[name][3]) : fallback);

  const STOPPED = () => tr(
    "The server has stopped: run `euromillions serve` again.",
    `Le serveur s’est arrêté${NNBSP}: relancez \`euromillions serve\`.`);

  // --- Requests ---

  async function request(method, path, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch (error) {
      throw new Error(STOPPED());
    }
    let data;
    try {
      data = await response.json();
    } catch (error) {
      throw new Error(tr(`Unexpected answer from the server (HTTP ${response.status}).`,
        `Réponse inattendue du serveur (HTTP ${response.status}).`));
    }
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  const get = (path) => request("GET", path);
  const send = (method, path, body) => request(method, path, body);

  // --- DOM ---

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "style") node.style.cssText = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value === true) node.setAttribute(key, "");
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function svg(markup) {
    const holder = document.createElement("span");
    holder.innerHTML = markup; // fixed icon markup only, never data
    return holder.firstElementChild;
  }

  const ICONS = {
    logo: '<svg width="28" height="28" viewBox="0 0 28 28" fill="none" stroke="#0b0b0b" stroke-width="1.6" aria-hidden="true"><circle cx="11" cy="15" r="8"/><circle cx="20.5" cy="8.5" r="5" fill="#f2b632"/></svg>',
    refresh: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9"/><path d="M13.5 2.5v3h-3"/></svg>',
    info: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="9" cy="9" r="7.25"/><path d="M9 8v5"/><path d="M9 5.2v.1"/></svg>',
    arrow: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8h10"/><path d="M9 4l4 4-4 4"/></svg>',
    copy: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="5" width="8.5" height="8.5" rx="1.5"/><path d="M10.5 5V3.5A1 1 0 0 0 9.5 2.5h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1H5"/></svg>',
    shuffle: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 4.5h3.2c1.6 0 2.6.8 3.4 2.2l.8 1.6c.8 1.4 1.8 2.2 3.4 2.2H14"/><path d="M2 11.5h3.2c.8 0 1.5-.2 2-.6"/><path d="M10 5.1c.5-.4 1.2-.6 2-.6H14"/><path d="M12.5 3 14 4.5 12.5 6"/><path d="M12.5 9 14 10.5 12.5 12"/></svg>',
    play: '<svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true"><path d="M3.5 2.2v9.6a.6.6 0 0 0 .9.5l7.6-4.8a.6.6 0 0 0 0-1L4.4 1.7a.6.6 0 0 0-.9.5z"/></svg>',
    check: '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 7.5l3 3 6-7"/></svg>',
  };
  const icon = (name) => svg(ICONS[name]);

  // --- Formatting, in the current language ---

  // The absolute value, grouped and with ``digits`` decimals: 2,353.17 or 2 353,17.
  const num = (x, digits = 0) =>
    Math.abs(x).toLocaleString(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits })
      .replace(/ | /g, NNBSP);
  // A minus sign, unless the figure shown rounds to zero.
  const negative = (x, text) => (x < 0 && /[1-9]/.test(text) ? MINUS + text : text);
  const fixed = (x, digits = 1) => negative(x, num(x, digits));
  const signed = (x, digits = 1) => {
    const text = num(x, digits);
    if (!/[1-9]/.test(text)) return text;
    return (x > 0 ? "+" : MINUS) + text;
  };
  const euro = (x, digits = 2) => negative(x, tr(`€${num(x, digits)}`, `${num(x, digits)}${NBSP}€`));
  const percent = (text) => tr(`${text}%`, `${text}${NNBSP}%`);
  const pct = (share, digits = 1) => negative(share, percent(num(share * 100, digits)));
  const signedPct = (share, digits = 1) => percent(signed(share * 100, digits));
  // Percentage points, for a spread around a percentage: "± 2.1 pts".
  const points = (share, digits = 1) => tr(`${num(share * 100, digits)} pts`, `${num(share * 100, digits)}${NBSP}pts`);
  const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

  // ``count(3, "draw", "tirage")``: "3 draws" or "3 tirages". Irregular nouns are given as
  // [singular, plural]. French puts 0 and 1 in the singular.
  function count(n, en, fr) {
    const forms = lang === "fr" ? fr : en;
    const [one, many] = Array.isArray(forms) ? forms : [forms, `${forms}s`];
    const singular = lang === "fr" ? Math.abs(n) < 2 : n === 1;
    return `${num(n)}${lang === "fr" ? NBSP : " "}${singular ? one : many}`;
  }

  const MONTHS = {
    en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    fr: ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
  };

  function date(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return `${d}${NBSP}${MONTHS[lang][m - 1]} ${y}`;
  }

  function monthYear(iso) {
    const [y, m] = iso.split("-").map(Number);
    return `${MONTHS[lang][m - 1]} ${y}`;
  }

  function longMonth(iso) {
    const [y, m] = iso.split("-").map(Number);
    return `${new Date(Date.UTC(y, m - 1, 15)).toLocaleString(locale(), { month: "long", timeZone: "UTC" })} ${y}`;
  }

  function parisParts(moment) {
    const parts = new Intl.DateTimeFormat(locale(), {
      timeZone: "Europe/Paris", weekday: "long", day: "numeric", month: "long", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(moment);
    return Object.fromEntries(parts.map((part) => [part.type, part.value]));
  }

  // "Friday 13 November 2026" / "Vendredi 13 novembre 2026"
  function longDate(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const parts = parisParts(new Date(Date.UTC(y, m - 1, d, 12)));
    return capitalize(`${parts.weekday} ${parts.day} ${parts.month} ${parts.year}`);
  }

  function countdown(moment, now = new Date()) {
    let minutes = Math.max(Math.floor((moment - now) / 60000), 0);
    const days = Math.floor(minutes / 1440);
    minutes -= days * 1440;
    const hours = Math.floor(minutes / 60);
    minutes -= hours * 60;
    const shown = [[days, "day", "jour"], [hours, "hour", "heure"], [minutes, "minute", "minute"]]
      .filter(([value]) => value)
      .map(([value, en, fr]) => count(value, en, fr));
    if (!shown.length) return tr("now", "maintenant");
    return tr(`in ${shown.slice(0, 2).join(" ")}`, `dans ${shown.slice(0, 2).join(" ")}`);
  }

  function zBin(z) {
    if (z >= 2) return { bg: "var(--z-high2)", fg: "#ffffff" };
    if (z >= 1) return { bg: "var(--z-high1)", fg: "#0b0b0b" };
    if (z > -1) return { bg: "var(--z-mid)", fg: "#0b0b0b" };
    if (z > -2) return { bg: "var(--z-low1)", fg: "#0b0b0b" };
    return { bg: "var(--z-low2)", fg: "#ffffff" };
  }

  function ball(n, { star = false, size = "" } = {}) {
    return el("span", {
      class: ["ball", star ? "star" : "", size].filter(Boolean).join(" "),
      "aria-label": star ? tr(`Lucky Star ${n}`, `Étoile ${n}`) : tr(`Number ${n}`, `Numéro ${n}`),
    }, String(n));
  }

  function line(numbers, stars, size = "") {
    return el("div", { class: "balls" },
      numbers.map((n) => ball(n, { size })),
      el("span", { class: "gap", "aria-hidden": "true" }),
      stars.map((n) => ball(n, { star: true, size })));
  }

  // --- The frame of every page ---

  let status = null;
  let active = "";
  let titles = null;
  let loader = null;

  function notice(message, kind = "") {
    const box = document.getElementById("notice");
    box.className = `notice ${kind}`;
    box.setAttribute("role", kind === "error" ? "alert" : "status");
    box.textContent = message || "";
  }

  function fetchSummary(result) {
    if (!result.downloaded) return tr("Already up to date.", "Déjà à jour.");
    const changes = [];
    if (result.added) {
      changes.push(tr(`added ${count(result.added, "new draw")}`,
        `${count(result.added, ["nouveau tirage", "nouveaux tirages"])} ${result.added > 1 ? "ajoutés" : "ajouté"}`));
    }
    if (result.updated) {
      changes.push(tr(`updated ${count(result.updated, "draw")}`,
        `${count(result.updated, "tirage")} ${result.updated > 1 ? "mis à jour" : "mis à jour"}`));
    }
    let text = capitalize(changes.join(", ") || tr("nothing new at the source", "rien de nouveau à la source")) + ".";
    if (result.unpublished) {
      text += tr(` The draw of ${date(result.unpublished)} has not been published yet.`,
        ` Le tirage du ${date(result.unpublished)} n’est pas encore publié.`);
    }
    return text;
  }

  async function update(button) {
    button.disabled = true;
    const label = button.lastChild;
    const before = label.textContent;
    label.textContent = tr("Updating…", "Mise à jour…");
    try {
      const result = await send("POST", "/api/fetch", {});
      status = result.status;
      renderFrame();
      notice(fetchSummary(result));
      await reload();
    } catch (error) {
      notice(tr(`The data could not be updated: ${error.message}`,
        `Les données n’ont pas pu être mises à jour${NNBSP}: ${error.message}`), "error");
    } finally {
      button.disabled = false;
      label.textContent = before;
    }
  }

  async function switchLanguage(next) {
    if (next === lang) return;
    lang = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (error) {
      // Not remembered, but the page still switches.
    }
    notice("");
    renderFrame();
    await reload();
  }

  function languageSwitch() {
    return el("div", { class: "segmented small-segmented", role: "group", "aria-label": tr("Language", "Langue") },
      [["en", "EN", "English"], ["fr", "FR", "Français"]].map(([code, short, name]) => el("button", {
        type: "button", lang: code, title: name, "aria-label": name, "aria-pressed": String(lang === code),
        onclick: () => switchLanguage(code),
      }, short)));
  }

  function renderHeader() {
    const header = document.getElementById("header");
    const summary = status && status.cache
      ? `${count(status.draws, "draw", "tirage")} · ${date(status.first)} – ${date(status.last)}`
      : tr("No draw history yet", "Pas encore d’historique");
    const button = el("button", { type: "button", class: "button", onclick: () => update(button) },
      icon("refresh"), el("span", {}, tr("Update data", "Mettre à jour")));
    header.replaceChildren(el("div", { class: "inner" },
      el("a", { class: "brand", href: "index.html" }, icon("logo"), "EuroMillions Analyzer"),
      el("nav", { class: "nav", "aria-label": tr("Sections", "Rubriques") },
        PAGES.map(([href, names]) => el("a", { href, "aria-current": href === active ? "page" : null }, tr(...names)))),
      el("div", { class: "data-status" },
        el("span", { class: "small muted" }, summary),
        status && status.cache && status.behind
          ? el("span", { class: "small muted" }, tr("· the latest draw is missing", "· le dernier tirage manque"))
          : null,
        button,
        languageSwitch())));
  }

  const DISCLAIMER_FR = "Les tirages sont aléatoires : les résultats passés ne permettent pas de prédire les suivants. " +
    "À des fins éducatives et de divertissement uniquement.";

  function renderFooter() {
    const english = (status ? status.disclaimer : "Lottery draws are random: past results cannot predict future ones.\n" +
      "For educational and entertainment purposes only.").split("\n").join(" ");
    document.getElementById("footer").replaceChildren(
      el("span", {}, tr(english, DISCLAIMER_FR.replace(" :", `${NNBSP}:`))),
      el("span", {}, tr("Data: official draw archives of the French operator, cached on this computer.",
        `Données${NNBSP}: archives officielles des tirages de la FDJ, conservées sur cet ordinateur.`)));
  }

  function renderFrame() {
    document.documentElement.lang = lang;
    if (titles) document.title = `${tr(...titles)} — EuroMillions Analyzer`;
    renderHeader();
    renderFooter();
  }

  function emptyState(container) {
    const button = el("button", { type: "button", class: "button primary", onclick: () => update(button) },
      icon("refresh"), el("span", {}, tr("Download the history", "Télécharger l’historique")));
    container.replaceChildren(el("section", { class: "card empty" },
      el("h2", {}, tr("No draw history yet", "Pas encore d’historique des tirages")),
      el("p", { class: "muted" }, tr(
        "Every draw since 2004 is downloaded from the official archives, in a few seconds. It is then kept on this computer.",
        "Tous les tirages depuis 2004 sont téléchargés depuis les archives officielles, en quelques secondes, puis conservés sur cet ordinateur.")),
      button));
  }

  function errorState(container, error) {
    container.replaceChildren(el("section", { class: "card empty" },
      el("h2", {}, tr("Something went wrong", "Un problème est survenu")),
      el("p", { class: "notice error" }, error.message)));
  }

  function disclaimer() {
    return el("p", { class: "disclaimer" }, icon("info"),
      el("span", {}, tr("Draws are random. Everything here describes past draws; none of it predicts the next one.",
        `Les tirages sont aléatoires. Tout ce qui figure ici décrit les tirages passés${NNBSP}; rien n’y prédit le prochain.`)));
  }

  async function reload() {
    const container = document.getElementById("content");
    if (!status || !status.cache) {
      emptyState(container);
      return;
    }
    try {
      await loader(status, container);
    } catch (error) {
      errorState(container, error);
    }
  }

  // Start a page: ``load(status, container)`` fills ``#content`` once a history exists. It is
  // called again after an update of the data and after a change of language.
  async function start(page, pageTitles, load) {
    active = page;
    titles = pageTitles;
    loader = load;
    renderFrame();
    try {
      status = await get("/api/status");
    } catch (error) {
      renderFrame();
      errorState(document.getElementById("content"), error);
      return;
    }
    renderFrame();
    await reload();
  }

  const currentStatus = () => status;

  return {
    tr, language, get, send, el, icon, ball, line, num, fixed, signed, euro, pct, signedPct, points,
    count, capitalize, date, monthYear, longMonth, longDate, parisParts, countdown, zBin, notice,
    disclaimer, start, currentStatus, strategyName, strategyDescription, NNBSP,
  };
})();
