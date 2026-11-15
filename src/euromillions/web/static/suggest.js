// The Suggest screen: lines for the next draw, from the strategy and settings chosen.
"use strict";

(() => {
  const { el, tr } = EM;
  const WINDOWS = [50, 100, 200];
  const MAX_LINES = 10;
  const state = { strategy: "balanced", lines: 5, window: 50, seed: null, copied: false };
  let status = null;
  let container = null;
  let result = null;
  let expectation = null;
  let error = "";
  let timer = null;
  let request = 0;

  EM.start("suggest.html", ["Suggest", "Suggestions"], async (current, target) => {
    status = current;
    container = target;
    container.replaceChildren(); // drawn again in full, in the current language
    if (!status.strategies.some((s) => s.name === state.strategy)) state.strategy = status.strategies[0].name;
    expectation = await lastBacktest();
    await suggest();
  });

  async function lastBacktest() {
    try {
      const job = await EM.get("/api/backtest");
      return job.state === "done" ? job.result : null;
    } catch (failure) {
      return null;
    }
  }

  function strategyInfo() {
    return status.strategies.find((s) => s.name === state.strategy);
  }

  async function suggest() {
    const mine = ++request;
    const picked = state.seed === null; // the server picks the seed: show it in the field
    const query = new URLSearchParams({ strategy: state.strategy, lines: state.lines, window: state.window });
    if (state.seed !== null) query.set("seed", state.seed);
    try {
      const answer = await EM.get(`/api/suggest?${query}`);
      if (mine !== request) return; // a newer request is on its way
      result = answer;
      state.seed = answer.seed;
      error = "";
    } catch (failure) {
      if (mine !== request) return;
      error = failure.message;
    }
    renderResults();
    const seed = document.getElementById("seed");
    if (seed && picked && state.seed !== null) seed.value = state.seed;
  }

  // Settings change the lines at once. Typing in the seed waits for a pause, and leaves the
  // field alone: rebuilding it under the caret would scramble what is being typed.
  function change(update, { wait = 0, redraw = true } = {}) {
    Object.assign(state, update, { copied: false });
    clearTimeout(timer);
    if (redraw) render();
    timer = setTimeout(suggest, wait);
  }

  function renderResults() {
    const box = document.getElementById("results");
    if (box) box.replaceChildren(lines(), expect());
    else render(); // the first time: draw the whole page
  }

  function render() {
    const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.focus : null;
    container.replaceChildren(
      el("div", { class: "intro" },
        el("h1", {}, tr(`Suggest lines for ${nextDrawName()}`, `Grilles pour le tirage du ${nextDrawName()}`)),
        el("p", {}, tr("Pick a strategy: the lines update as you change the settings. The same seed always gives the same lines.",
          `Choisissez une stratégie${EM.NNBSP}: les grilles suivent vos réglages. La même graine donne toujours les mêmes grilles.`))),
      el("div", { class: "split" },
        el("div", { class: "side", style: "flex-basis: 340px" }, settings()),
        el("div", { class: "wide", id: "results", style: "gap: 16px" }, lines(), expect())));
    if (focused) {
      const again = container.querySelector(`[data-focus="${focused}"]`);
      if (again) again.focus();
    }
  }

  function nextDrawName() {
    const parts = EM.parisParts(new Date(status.nextDraw));
    return `${parts.weekday} ${parts.day} ${parts.month}`;
  }

  // --- Settings ---

  function settings() {
    const info = strategyInfo();
    const cost = EM.euro(state.lines * status.linePrice);
    const seedInput = el("input", {
      id: "seed", type: "text", inputmode: "numeric", value: state.seed === null ? "" : state.seed,
      "data-focus": "seed", "aria-describedby": "seedHint", style: "flex: 1; min-width: 0",
      oninput: (event) => {
        const text = event.target.value.trim();
        if (/^-?\d+$/.test(text)) change({ seed: Number(text) }, { wait: 400, redraw: false });
      },
    });
    const lineCount = EM.count(state.lines, "line", "grille");
    return el("section", { class: "card", "aria-label": tr("Settings", "Réglages"), style: "gap: 24px" },
      el("div", { class: "field", role: "group", "aria-labelledby": "strategyLabel" },
        el("span", { class: "label", id: "strategyLabel" }, tr("Strategy", "Stratégie")),
        status.strategies.map((s) => el("button", {
          type: "button", class: "strategy-option", "aria-pressed": String(s.name === state.strategy),
          "data-focus": `strategy-${s.name}`, onclick: () => change({ strategy: s.name }),
        }, el("span", { class: "name" }, EM.capitalize(EM.strategyName(s.name))),
        el("span", { class: "small muted" }, EM.strategyDescription(s.name, EM.capitalize(s.description)))))),
      el("div", { class: "field", role: "group", "aria-labelledby": "linesLabel" },
        el("span", { class: "label", id: "linesLabel" }, tr("Lines", "Grilles")),
        el("div", { class: "chips" }, Array.from({ length: MAX_LINES }, (_, i) => i + 1).map((n) => el("button", {
          type: "button", class: "pill", "aria-pressed": String(n === state.lines), "data-focus": `lines-${n}`,
          onclick: () => change({ lines: n }),
        }, n))),
        el("span", { class: "hint" }, tr(`${lineCount} ${state.lines === 1 ? "costs" : "cost"} ${cost} a draw in France.`,
          `${lineCount}${EM.NNBSP}: ${cost} par tirage en France.`))),
      info.usesWindow
        ? el("div", { class: "field" },
          el("label", { for: "window" }, tr("Recent window", "Période récente")),
          el("select", { id: "window", "data-focus": "window", onchange: (event) => change({ window: Number(event.target.value) }) },
            WINDOWS.map((w) => el("option", { value: w, selected: w === state.window }, tr(`Last ${w} draws`, `${w} derniers tirages`)))),
          el("span", { class: "hint" }, tr("Which draws count as recent for hot numbers.",
            "Les tirages qui comptent comme récents pour les numéros chauds.")))
        : el("div", { class: "field" }, el("span", { class: "label" }, tr("Recent window", "Période récente")),
          el("span", { class: "hint" }, tr("Not used by this strategy.", "Pas utilisée par cette stratégie."))),
      el("div", { class: "field" },
        el("label", { for: "seed" }, tr("Seed", "Graine")),
        el("div", { style: "display: flex; gap: 8px" }, seedInput,
          el("button", {
            type: "button", class: "button", "data-focus": "reseed",
            onclick: () => change({ seed: null }),
          }, EM.icon("shuffle"), tr("New seed", "Nouvelle graine"))),
        el("span", { class: "hint", id: "seedHint" }, tr("A whole number. The same seed gives the same lines.",
          "Un nombre entier. La même graine donne les mêmes grilles."))));
  }

  // --- Lines ---

  async function copy() {
    const text = result.lines.map((line) => line.text).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      state.copied = true;
      renderResults();
    } catch (failure) {
      EM.notice(tr("The browser did not let the page copy: select the lines and copy them by hand.",
        `Le navigateur n’a pas autorisé la copie${EM.NNBSP}: sélectionnez les grilles et copiez-les à la main.`), "error");
    }
  }

  function lines() {
    if (error) return el("section", { class: "card" }, el("p", { class: "notice error" }, error));
    if (!result) return el("section", { class: "card" }, el("p", { class: "muted" }, tr("Suggesting…", "Calcul…")));
    const button = el("button", { type: "button", class: "button primary", "data-focus": "copy", onclick: copy },
      EM.icon("copy"), state.copied ? tr("Copied", "Copiées") : tr("Copy lines", "Copier les grilles"));
    const basedOn = EM.count(result.basedOn.draws, "draw", "tirage");
    return el("section", { class: "card", "aria-labelledby": "linesTitle", style: "gap: 8px" },
      el("div", { class: "card-head", style: "align-items: center" },
        el("div", {},
          el("h2", { id: "linesTitle" }, `${EM.count(result.lines.length, "line", "grille")} · ${EM.strategyName(result.strategy)}`),
          el("p", { class: "small muted" }, tr(
            `Seed ${result.seed} · based on ${basedOn} up to ${EM.date(result.basedOn.last)}`,
            `Graine ${result.seed} · d’après ${basedOn} jusqu’au ${EM.date(result.basedOn.last)}`))),
        button),
      el("div", { style: "margin-top: 8px" }, result.lines.map((line, i) => el("div", { class: "line-row" },
        el("span", { class: "index" }, i + 1),
        EM.line(line.numbers, line.stars),
        el("div", { class: "chips" },
          el("span", { class: "chip" }, tr(`sum ${line.sum}`, `somme ${line.sum}`)),
          el("span", { class: "chip" }, tr(`${line.odd} odd · ${5 - line.odd} even`,
            `${line.odd} ${line.odd > 1 ? "impairs" : "impair"} · ${5 - line.odd} ${5 - line.odd > 1 ? "pairs" : "pair"}`)),
          el("span", { class: "chip" }, tr(`${line.low} low · ${5 - line.low} high`,
            `${line.low} bas · ${5 - line.low} ${5 - line.low > 1 ? "hauts" : "haut"}`)))))));
  }

  function expect() {
    let text = tr("In the backtest, every strategy loses about as much as random lines: none beats chance. Pick whichever lines you like.",
      `Au backtest, chaque stratégie perd à peu près autant que des grilles au hasard${EM.NNBSP}: aucune ne bat le hasard. Jouez les grilles qui vous plaisent.`);
    if (expectation) {
      const row = expectation.results.find((r) => r.strategy === state.strategy);
      const random = expectation.results.find((r) => r.strategy === "random");
      if (row) {
        const lost = (r) => EM.pct(-r.roi);
        const draws = EM.count(expectation.testDraws, "draw", "tirage");
        const chance = `${EM.pct(-expectation.chance.roi)} ± ${EM.points(expectation.chance.roiSd)}`;
        text = tr(
          `In the last backtest (${draws}), ${EM.strategyName(state.strategy)} lines lost ${lost(row)} of their stakes` +
          (random ? `, random lines ${lost(random)}` : "") +
          `, and chance alone loses ${chance}. No strategy beats chance: pick whichever lines you like.`,
          `Au dernier backtest (${draws}), la stratégie ${EM.strategyName(state.strategy)} a perdu ${lost(row)} des mises` +
          (random ? `, les grilles au hasard ${lost(random)}` : "") +
          `, et le hasard seul fait perdre ${chance}. Aucune stratégie ne bat le hasard${EM.NNBSP}: jouez les grilles qui vous plaisent.`);
      }
    }
    return el("section", { class: "card", style: "flex-direction: row; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px 24px" },
      el("div", { style: "display: flex; flex-direction: column; gap: 4px; flex: 1 1 360px; min-width: 0" },
        el("h3", { style: "font-size: 16px" }, tr("What to expect", "À quoi s’attendre")),
        el("p", { class: "muted" }, text)),
      el("a", { class: "button", href: "backtest.html" }, tr("See the backtest", "Voir le backtest"), EM.icon("arrow")));
  }
})();
