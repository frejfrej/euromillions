// EuroMillions Analyzer: what every page shares. Every figure comes from the server;
// this file only fetches, formats and lays out.
"use strict";

const EM = (() => {
  const STOPPED = "The server has stopped: run `euromillions serve` again.";
  const PAGES = [
    ["index.html", "Numbers"],
    ["patterns.html", "Patterns"],
    ["suggest.html", "Suggest"],
    ["backtest.html", "Backtest"],
  ];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MINUS = "−";

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
      throw new Error(STOPPED);
    }
    let data;
    try {
      data = await response.json();
    } catch (error) {
      throw new Error(`Unexpected answer from the server (HTTP ${response.status}).`);
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

  // --- Formatting ---

  const sign = (x) => (x > 0 ? "+" : x < 0 ? MINUS : "");
  const signed = (x, digits = 1) => {
    const rounded = Number(Math.abs(x).toFixed(digits));
    return (rounded === 0 ? "" : sign(x)) + rounded.toFixed(digits);
  };
  const num = (x, digits = 0) =>
    Math.abs(x).toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  // A minus sign, unless the amount shown rounds to zero.
  const negative = (x, text) => (x < 0 && /[1-9]/.test(text) ? MINUS + text : text);
  const euro = (x, digits = 2) => negative(x, `€${num(x, digits)}`);
  const pct = (share, digits = 1) => negative(share, `${num(share * 100, digits)}%`);
  const signedPct = (share, digits = 1) => sign(share) + `${num(share * 100, digits)}%`;
  const plural = (n, noun, many) => `${num(n)} ${n === 1 ? noun : many || `${noun}s`}`;
  const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

  function date(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return `${d} ${MONTHS[m - 1]} ${y}`;
  }

  function parisParts(moment) {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Paris", weekday: "long", day: "numeric", month: "long", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(moment);
    return Object.fromEntries(parts.map((part) => [part.type, part.value]));
  }

  function longDate(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const parts = parisParts(new Date(Date.UTC(y, m - 1, d, 12)));
    return `${parts.weekday} ${parts.day} ${parts.month} ${parts.year}`;
  }

  function countdown(moment, now = new Date()) {
    let minutes = Math.max(Math.floor((moment - now) / 60000), 0);
    const days = Math.floor(minutes / 1440);
    minutes -= days * 1440;
    const hours = Math.floor(minutes / 60);
    minutes -= hours * 60;
    const shown = [[days, "day"], [hours, "hour"], [minutes, "minute"]]
      .filter(([value]) => value)
      .map(([value, unit]) => plural(value, unit));
    return shown.length ? `in ${shown.slice(0, 2).join(" ")}` : "now";
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
      "aria-label": star ? `Lucky Star ${n}` : `Number ${n}`,
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

  function notice(message, kind = "") {
    const box = document.getElementById("notice");
    box.className = `notice ${kind}`;
    box.setAttribute("role", kind === "error" ? "alert" : "status");
    box.textContent = message || "";
  }

  function fetchSummary(result) {
    if (!result.downloaded) return "Already up to date.";
    const changes = [];
    if (result.added) changes.push(`added ${plural(result.added, "new draw")}`);
    if (result.updated) changes.push(`updated ${plural(result.updated, "draw")}`);
    let text = capitalize(changes.join(", ") || "nothing new at the source") + ".";
    if (result.unpublished) text += ` The draw of ${date(result.unpublished)} has not been published yet.`;
    return text;
  }

  async function update(button) {
    button.disabled = true;
    const label = button.lastChild;
    const before = label.textContent;
    label.textContent = "Updating…";
    try {
      const result = await send("POST", "/api/fetch", {});
      status = result.status;
      renderHeader();
      notice(fetchSummary(result));
      await reload();
    } catch (error) {
      notice(`The data could not be updated: ${error.message}`, "error");
    } finally {
      button.disabled = false;
      label.textContent = before;
    }
  }

  let active = "";
  let loader = null;

  function renderHeader() {
    const header = document.getElementById("header");
    const summary = status && status.cache
      ? `${plural(status.draws, "draw")} · ${date(status.first)} – ${date(status.last)}`
      : "No draw history yet";
    const button = el("button", { type: "button", class: "button", onclick: () => update(button) },
      icon("refresh"), el("span", {}, "Update data"));
    header.replaceChildren(el("div", { class: "inner" },
      el("a", { class: "brand", href: "index.html" }, icon("logo"), "EuroMillions Analyzer"),
      el("nav", { class: "nav", "aria-label": "Sections" },
        PAGES.map(([href, name]) => el("a", { href, "aria-current": href === active ? "page" : null }, name))),
      el("div", { class: "data-status" },
        el("span", { class: "small muted" }, summary),
        status && status.cache && status.behind
          ? el("span", { class: "small muted" }, "· the latest draw is missing")
          : null,
        button)));
  }

  function renderFooter() {
    const lines = (status ? status.disclaimer : "").split("\n");
    document.getElementById("footer").replaceChildren(
      el("span", {}, lines.join(" ")),
      el("span", {}, "Data: official draw archives of the French operator, cached on this computer."));
  }

  function emptyState(container) {
    const button = el("button", { type: "button", class: "button primary", onclick: () => update(button) },
      icon("refresh"), el("span", {}, "Download the history"));
    container.replaceChildren(el("section", { class: "card empty" },
      el("h2", {}, "No draw history yet"),
      el("p", { class: "muted" }, "Every draw since 2004 is downloaded from the official archives, in a few seconds. It is then kept on this computer."),
      button));
  }

  function errorState(container, error) {
    container.replaceChildren(el("section", { class: "card empty" },
      el("h2", {}, "Something went wrong"),
      el("p", { class: "notice error" }, error.message)));
  }

  function disclaimer() {
    return el("p", { class: "disclaimer" }, icon("info"),
      el("span", {}, "Draws are random. Everything here describes past draws; none of it predicts the next one."));
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

  // Start a page: ``load(status, container)`` fills ``#content`` once a history exists.
  async function start(page, load) {
    active = page;
    loader = load;
    renderHeader();
    try {
      status = await get("/api/status");
    } catch (error) {
      renderHeader();
      errorState(document.getElementById("content"), error);
      return;
    }
    renderHeader();
    renderFooter();
    await reload();
  }

  const currentStatus = () => status;

  return {
    get, send, el, icon, ball, line, signed, num, euro, pct, signedPct, plural, capitalize,
    date, longDate, parisParts, countdown, zBin, notice, disclaimer, start, currentStatus,
  };
})();
