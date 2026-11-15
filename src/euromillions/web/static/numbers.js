// The Numbers screen: next and latest draw, every number and star against chance, rankings.
"use strict";

(() => {
  const { el } = EM;
  const WINDOWS = [50, 100, 200];
  const SHOWN = 6; // entries per ranking card
  const state = { window: 50, recent: false, kind: "main", n: null };
  let analysis = null;
  let status = null;
  let container = null;
  let clock = null;

  EM.start("index.html", async (current, target) => {
    status = current;
    container = target;
    await refresh();
  });

  async function refresh() {
    analysis = await EM.get(`/api/analysis?window=${state.window}&top=${SHOWN}`);
    if (state.n === null) state.n = analysis.mostDrawn.length ? analysis.mostDrawn[0].number : 1;
    render();
  }

  function render(focusKey) {
    container.replaceChildren(draws(), EM.disclaimer(), numbersAndDetail(), rankings());
    if (focusKey) {
      const button = container.querySelector(`[data-key="${focusKey}"]`);
      if (button) button.focus();
    }
  }

  // --- Next and latest draw ---

  function draws() {
    const next = new Date(status.nextDraw);
    const parts = EM.parisParts(next);
    const when = el("span", {}, `${parts.hour}:${parts.minute} Paris time · ${EM.countdown(next)}`);
    clearInterval(clock);
    clock = setInterval(() => {
      when.textContent = `${parts.hour}:${parts.minute} Paris time · ${EM.countdown(next)}`;
    }, 60000);

    const latest = status.latest;
    const jackpot = latest.jackpotWon === null ? "" : latest.jackpotWon ? "Jackpot won" : "Jackpot not won";
    return el("section", { class: "row", "aria-label": "Draws" },
      el("div", { class: "card", style: "gap: 6px" },
        el("p", { class: "eyebrow" }, "Next draw"),
        el("p", { style: "font-size: 28px; font-weight: 650; line-height: 1.2" },
          `${parts.weekday} ${parts.day} ${parts.month}`),
        el("p", { class: "muted" }, when)),
      el("div", { class: "card", style: "gap: 12px; flex-grow: 2" },
        el("p", { class: "eyebrow" }, `Latest draw · ${EM.longDate(latest.date)}`),
        el("div", { class: "balls" }, EM.line(latest.numbers, latest.stars),
          jackpot ? el("span", { class: "muted", style: "margin-left: 8px" }, jackpot) : null)));
  }

  // --- The grids and the details of the selected number ---

  function statsOf(kind) {
    if (kind === "star") return state.recent ? analysis.recentStars : analysis.stars;
    return state.recent ? analysis.recentNumbers : analysis.numbers;
  }

  function select(kind, n, key) {
    state.kind = kind;
    state.n = n;
    render(key);
  }

  function cell(kind, stats) {
    const bin = EM.zBin(stats.z);
    const key = `${kind}-${stats.number}`;
    const label = `${kind === "star" ? "Star" : "Number"} ${stats.number}: drawn ${EM.plural(stats.count, "time")}, z-score ${EM.signed(stats.z, 2)}`;
    return el("button", {
      type: "button", class: "cell", "data-key": key, title: label, "aria-label": label,
      "aria-pressed": String(state.kind === kind && state.n === stats.number),
      style: `background: ${bin.bg}; color: ${bin.fg}`,
      onclick: () => select(kind, stats.number, key),
    }, el("span", { class: "n" }, stats.number), el("span", { class: "z", "aria-hidden": "true" }, EM.signed(stats.z, 1)));
  }

  function periodControls() {
    const toggle = (recent, text) => el("button", {
      type: "button", "aria-pressed": String(state.recent === recent),
      onclick: () => { state.recent = recent; render(); },
    }, text);
    const select = el("select", {
      id: "window", onchange: async (event) => {
        state.window = Number(event.target.value);
        await refresh();
      },
    }, WINDOWS.map((w) => el("option", { value: w, selected: w === state.window }, `Last ${w} draws`)));
    return el("div", { class: "row", style: "align-items: flex-end; gap: 12px 24px" },
      el("div", { class: "field", style: "flex: 0 1 auto; min-width: 0" },
        el("span", { class: "label", id: "periodLabel" }, "Period"),
        el("div", { class: "segmented", role: "group", "aria-labelledby": "periodLabel" },
          toggle(false, "Full history"), toggle(true, `Last ${analysis.window} draws`))),
      el("div", { class: "field", style: "flex: 0 1 auto; min-width: 0" },
        el("label", { for: "window" }, "Recent window"), select));
  }

  function legend() {
    const item = (color, text, ring) => el("span", {},
      el("span", { class: "swatch", style: `background: ${color}${ring ? "; box-shadow: inset 0 0 0 1px #d6d4cc" : ""}` }), text);
    return el("div", { class: "legend" },
      el("span", { style: "font-weight: 650; color: var(--ink)" }, "z-score"),
      item("var(--z-low2)", "−2 or less"), item("var(--z-low1)", "−2 to −1"),
      item("var(--z-mid)", "−1 to +1", true), item("var(--z-high1)", "+1 to +2"),
      item("var(--z-high2)", "+2 or more"));
  }

  function poolNote() {
    const since = Object.entries(analysis.starPoolSince);
    const byDate = {};
    for (const [star, day] of since) (byDate[day] = byDate[day] || []).push(star);
    const parts = Object.entries(byDate).map(([day, stars]) => {
      const [y, m] = day.split("-").map(Number);
      const month = new Date(Date.UTC(y, m - 1, 15)).toLocaleString("en-GB", { month: "long", timeZone: "UTC" });
      const names = stars.length === 1 ? `star ${stars[0]}` : `stars ${stars.slice(0, -1).join(", ")} and ${stars[stars.length - 1]}`;
      return `${names} in ${month} ${y}`;
    });
    return `${EM.capitalize(parts.join(", "))} joined the pool: each star is compared with the draws it could come up in.`;
  }

  function numbersAndDetail() {
    const period = state.recent ? `in the last ${analysis.window} draws` : `over all ${EM.num(analysis.totalDraws)} draws`;
    const chance = analysis.chance;
    const mainCard = el("section", { class: "card", "aria-labelledby": "mainTitle" },
      el("div", { class: "card-head" },
        el("div", {},
          el("h2", { id: "mainTitle" }, "Main numbers"),
          el("p", { class: "muted" }, `Times drawn against what fair draws would give, ${period}.`))),
      periodControls(),
      el("div", { class: "scroll-x" },
        el("div", { class: "number-grid main" }, statsOf("main").map((s) => cell("main", s)))),
      legend(),
      el("p", { class: "small muted" },
        "Blue: drawn more often than expected; red: less often. The z-score is the distance to the " +
        "expected count, in standard deviations. Fair draws typically put the most extreme of 50 " +
        `z-scores near ±${chance.extremeZ.main.toFixed(1)}, so even the darkest cell is what chance alone produces.`));
    const starCard = el("section", { class: "card", "aria-labelledby": "starTitle" },
      el("div", { class: "card-head" },
        el("div", {},
          el("h2", { id: "starTitle" }, "Lucky Stars"),
          el("p", { class: "muted" }, poolNote()))),
      el("div", { class: "scroll-x" },
        el("div", { class: "number-grid stars" }, statsOf("star").map((s) => cell("star", s)))),
      el("p", { class: "small muted" },
        `Same colours as above. For 12 stars, fair draws put the most extreme z-score near ±${chance.extremeZ.star.toFixed(1)}.`));
    return el("div", { class: "split" },
      el("div", { class: "wide" }, mainCard, starCard),
      el("div", { class: "side" }, detail()));
  }

  function detail() {
    const star = state.kind === "star";
    const s = statsOf(state.kind).find((entry) => entry.number === state.n);
    const kind = star ? "star" : "main";
    const rows = [
      ["Times drawn", EM.num(s.count), `${state.recent ? `in ${analysis.window} draws · ` : ""}expected ${s.expected.toFixed(1)}`],
      ["z-score", EM.signed(s.z, 2), ""],
      ["Share of draws", EM.pct(s.frequency, 1), ""],
      ["Average gap", s.averageGap === null ? "—" : `${s.averageGap.toFixed(1)} draws`, ""],
      ["Current gap", s.currentGap === 0 ? "drawn last time" : EM.plural(s.currentGap, "draw"), ""],
      ["Longest gap", EM.plural(s.longestGap, "draw"), ""],
      ["Last seen", s.lastSeen ? EM.date(s.lastSeen) : "not in this period", ""],
    ];
    const since = star ? analysis.starPoolSince[s.number] : null;
    if (since) rows.push(["In the pool since", EM.date(since), ""]);
    const total = state.recent ? Math.min(analysis.window, analysis.totalDraws) : analysis.totalDraws;
    if (s.eligible < total) rows.push(["Draws it could come up in", EM.num(s.eligible), ""]);
    const summary = s.z >= 1 ? "Drawn more often than expected" : s.z <= -1 ? "Drawn less often than expected" : "Close to what fair draws give";
    const pool = star ? 12 : 50;
    return el("aside", { class: "card", "aria-labelledby": "detailTitle" },
      el("p", { class: "eyebrow" }, `${star ? "Lucky Star" : "Main number"} · ${state.recent ? `last ${analysis.window} draws` : "full history"}`),
      el("div", { style: "display: flex; align-items: center; gap: 16px" },
        EM.ball(s.number, { star, size: "large" }),
        el("div", { style: "display: flex; flex-direction: column; gap: 2px; min-width: 0" },
          el("h2", { id: "detailTitle" }, `${star ? "Star" : "Number"} ${s.number}`),
          el("p", { class: "muted" }, summary))),
      el("div", {}, rows.map(([label, value, note]) => el("div", { class: "list-row" },
        el("span", { class: "muted" }, label),
        el("span", { class: "value" }, el("strong", {}, value), note ? el("span", { class: "small muted" }, ` ${note}`) : null)))),
      el("p", { class: "note" },
        `Fair draws typically put the most extreme of ${pool} z-scores near ±${analysis.chance.extremeZ[kind].toFixed(1)}, ` +
        `and the longest current absence near ${analysis.chance.longestGap[kind]} draws.` +
        (state.recent ? " Gaps are counted within the period shown." : "")));
  }

  // --- Rankings ---

  function rankingCard(title, sub, entries, starEntries, describe, note) {
    return el("section", { class: "card", style: "gap: 12px" },
      el("div", { style: "display: flex; flex-direction: column; gap: 2px" },
        el("h3", {}, title), el("p", { class: "small muted" }, sub)),
      el("div", {}, entries.map((s) => {
        const [main, side] = describe(s);
        return el("div", { class: "ranking-entry" }, EM.ball(s.number, { size: "small" }),
          el("span", { class: "what" }, main), el("span", { class: "small muted num" }, side));
      })),
      starEntries.length
        ? el("div", { class: "chips", "aria-label": "Lucky Stars" },
          el("span", { class: "small muted", style: "align-self: center" }, "Stars"),
          starEntries.slice(0, 3).map((s) => el("span", { class: "chip" }, `${s.number} · ${describe(s).join(" · ")}`)))
        : null,
      el("p", { class: "small muted", style: "border-top: 1px solid var(--line-soft); padding-top: 10px" }, note));
  }

  function rankings() {
    const counted = (s) => [EM.plural(s.count, "time"), `z ${EM.signed(s.z, 1)}`];
    const absent = (s) => [EM.plural(s.currentGap, "draw"), s.lastSeen ? `since ${EM.date(s.lastSeen)}` : "never drawn"];
    const z = analysis.chance.extremeZ.main.toFixed(1);
    const w = analysis.window;
    return el("section", { class: "cards", "aria-label": "Rankings" },
      rankingCard("Most drawn", "Over the whole history, against what each number could do",
        analysis.mostDrawn, analysis.mostDrawnStars, counted, `Fair draws put the top of 50 z-scores near +${z}.`),
      rankingCard("Least drawn", "Over the whole history",
        analysis.leastDrawn, analysis.leastDrawnStars, counted, `Fair draws put the bottom of 50 z-scores near −${z}.`),
      rankingCard(`Hot · last ${w} draws`, "Drawn more often than expected recently",
        analysis.hot, analysis.hotStars, counted, `Fair draws put the top of 50 z-scores near +${z}.`),
      rankingCard(`Cold · last ${w} draws`, "Drawn less often than expected recently",
        analysis.cold, analysis.coldStars, counted, `Fair draws put the bottom of 50 z-scores near −${z}.`),
      rankingCard("Most overdue", "Longest wait since the last appearance",
        analysis.overdue, analysis.overdueStars, absent,
        `Fair draws put the longest current absence near ${analysis.chance.longestGap.main} draws (${analysis.chance.longestGap.star} for stars).`));
  }
})();
