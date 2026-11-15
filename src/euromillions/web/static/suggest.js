// The Suggest screen: lines for the next draw, from the strategy and settings chosen.
"use strict";

(() => {
  const { el, tr } = EM;
  const DRAWS_A_YEAR = 104; // Tuesdays and Fridays
  // ``lines`` is 1 or more, 0 when the budget pays for none, and null when the lines and
  // the budget are both left empty: the page then shows the number that maximizes the
  // expected winnings. ``budget`` is the amount typed, or the cost of the lines typed.
  const state = { strategy: "balanced", lines: 5, budget: 12.5, window: 50, seed: null, copied: false };
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
    if (!state.lines) { // no line to suggest: the optimum is shown instead
      renderResults();
      return;
    }
    const picked = state.seed === null; // the server picks the seed: show it in the field
    const query = new URLSearchParams({ strategy: state.strategy, lines: state.lines, window: state.window });
    if (state.seed !== null) query.set("seed", state.seed);
    try {
      const answer = await EM.get(`/api/suggest?${query}`);
      if (mine !== request) return; // a newer request is on its way
      result = answer;
      // Keep the seed as text: a JSON number loses the digits of seeds beyond 2^53.
      if (picked) state.seed = String(answer.seed);
      else result.seed = state.seed;
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
    if (box) box.replaceChildren(...results());
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
        el("div", { class: "wide", id: "results", style: "gap: 16px" }, results())));
    if (focused) {
      const again = container.querySelector(`[data-focus="${focused}"]`);
      if (again) again.focus();
    }
  }

  function nextDrawName() {
    const parts = EM.parisParts(new Date(status.nextDraw));
    return `${parts.weekday} ${parts.day} ${parts.month}`;
  }

  function results() {
    return state.lines ? [lines(), expect()] : [optimum()];
  }

  // --- Settings ---

  const seedHint = () => tr("A whole number. The same seed gives the same lines.",
    "Un nombre entier. La même graine donne les mêmes grilles.");

  const MONEY = 1e-9; // tolerance on amounts in euros

  function linesHint() {
    const price = status.linePrice;
    if (state.lines === null) {
      return tr("Leave both empty for the number of lines that maximizes the expected winnings.",
        "Laissez les deux vides pour le nombre de grilles qui maximise les gains espérés.");
    }
    if (state.lines === 0) {
      return tr(`${EM.euro(state.budget)} pays for no line: a line costs ${EM.euro(price)}.`,
        `${EM.euro(state.budget)} ne paie aucune grille${EM.NNBSP}: une grille coûte ${EM.euro(price)}.`);
    }
    const lineCount = EM.count(state.lines, "line", "grille");
    const cost = EM.euro(state.lines * price);
    if (Math.abs(state.budget - state.lines * price) > MONEY) {
      return tr(`${EM.euro(state.budget)} pays for ${lineCount}, for ${cost} a draw.`,
        `${EM.euro(state.budget)} paie ${lineCount}, soit ${cost} par tirage.`);
    }
    return tr(`${lineCount} ${state.lines === 1 ? "costs" : "cost"} ${cost} a draw in France.`,
      `${lineCount}${EM.NNBSP}: ${cost} par tirage en France.`);
  }

  // Type the lines or the budget: the other field follows, and neither is rebuilt under the caret.
  function linesFields() {
    const price = status.linePrice;
    const show = (message, wrong) => {
      const hint = document.getElementById("linesHint");
      hint.textContent = message;
      hint.classList.toggle("error", Boolean(wrong));
      for (const id of ["lines", "budget"]) {
        document.getElementById(id).setAttribute("aria-invalid", String(id === wrong));
      }
    };
    const update = (lines, budget, other, value) => {
      document.getElementById(other).value = value;
      change({ lines, budget }, { wait: 300, redraw: false });
      show(linesHint(), null);
    };
    const linesInput = el("input", {
      id: "lines", type: "number", inputmode: "numeric", min: 0, step: 1, placeholder: "—",
      value: state.lines === null ? "" : state.lines, "aria-describedby": "linesHint",
      oninput: (event) => {
        const text = event.target.value.trim();
        const n = Number(text);
        if (text === "" && !event.target.validity.badInput) update(null, null, "budget", "");
        else if (Number.isInteger(n) && n >= 1) {
          const cost = Math.round(n * price * 100) / 100;
          update(n, cost, "budget", cost);
        } else {
          show(tr("A whole number, 1 or more, or empty.", "Un nombre entier, 1 ou plus, ou vide."), "lines");
        }
      },
    });
    const budgetInput = el("input", {
      id: "budget", type: "number", inputmode: "decimal", min: 0, step: "any", placeholder: "—",
      value: state.budget === null ? "" : state.budget, "aria-describedby": "linesHint",
      oninput: (event) => {
        const text = event.target.value.trim();
        const amount = Number(text);
        if (text === "" && !event.target.validity.badInput) update(null, null, "lines", "");
        else if (Number.isFinite(amount) && amount >= 0) {
          const n = Math.floor(amount / price + MONEY);
          update(n, amount, "lines", n);
        } else {
          show(tr("An amount in euros, or empty.", "Un montant en euros, ou vide."), "budget");
        }
      },
    });
    const field = (id, text, input) => el("div", { class: "field", style: "flex: 1 1 110px; min-width: 0" },
      el("label", { for: id }, text), input);
    return el("div", { class: "field", role: "group", "aria-label": tr("Lines or budget", "Grilles ou budget") },
      el("div", { style: "display: flex; flex-wrap: wrap; gap: 8px 12px" },
        field("lines", tr("Lines", "Grilles"), linesInput),
        field("budget", tr("Budget (€)", "Budget (€)"), budgetInput)),
      el("span", { class: "hint", id: "linesHint" }, linesHint()));
  }

  function settings() {
    const info = strategyInfo();
    const seedInput = el("input", {
      id: "seed", type: "text", inputmode: "numeric", value: state.seed === null ? "" : state.seed,
      "data-focus": "seed", "aria-describedby": "seedHint", style: "flex: 1; min-width: 0",
      oninput: (event) => {
        const text = event.target.value.trim();
        const valid = /^-?\d+$/.test(text);
        event.target.setAttribute("aria-invalid", String(!valid));
        const hint = document.getElementById("seedHint");
        hint.classList.toggle("error", !valid);
        hint.textContent = valid ? seedHint() : tr("Not a whole number: the lines below are unchanged.",
          `Ce n’est pas un nombre entier${EM.NNBSP}: les grilles ci-dessous n’ont pas changé.`);
        if (valid) change({ seed: BigInt(text).toString() }, { wait: 400, redraw: false });
      },
    });
    return el("section", { class: "card", "aria-label": tr("Settings", "Réglages"), style: "gap: 24px" },
      el("div", { class: "field", role: "group", "aria-labelledby": "strategyLabel" },
        el("span", { class: "label", id: "strategyLabel" }, tr("Strategy", "Stratégie")),
        status.strategies.map((s) => el("button", {
          type: "button", class: "strategy-option", "aria-pressed": String(s.name === state.strategy),
          "data-focus": `strategy-${s.name}`, onclick: () => change({ strategy: s.name }),
        }, el("span", { class: "name" }, EM.capitalize(EM.strategyName(s.name))),
        el("span", { class: "small muted" }, EM.strategyDescription(s.name, EM.capitalize(s.description)))))),
      linesFields(),
      info.usesWindow
        ? el("div", { class: "field" },
          el("span", { class: "label", id: "windowLabel" }, tr("Recent window", "Période récente")),
          EM.windowChoice(state.window, (w) => change({ window: w }), "windowLabel"),
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
        el("span", { class: "hint", id: "seedHint" }, seedHint())));
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

  // What a line returned on average, and what it lost: from the server, or the rule of the game.
  function averageLoss() {
    const price = status.linePrice;
    const average = status.averageReturn;
    return average ? { back: average.amount, loss: price - average.amount, draws: average.draws } : null;
  }

  function optimum() {
    const price = status.linePrice;
    const average = averageLoss();
    const since = EM.date(status.currentRulesSince);
    const why = average
      ? tr(`Over the ${EM.count(average.draws, "draw")} since ${since}, the prizes paid came to ${EM.euro(average.back)} ` +
        `per ${EM.euro(price)} line: ${EM.euro(average.loss)} lost per line on average, ${EM.pct(average.loss / price, 0)} of the stake. ` +
        "No strategy changes that: each extra line adds to the expected loss, so the number of lines that maximizes " +
        "the expected winnings is zero.",
      `Sur les ${EM.count(average.draws, "draw", "tirage")} depuis le ${since}, les gains versés représentent ${EM.euro(average.back)} ` +
        `par grille de ${EM.euro(price)}${EM.NNBSP}: ${EM.euro(average.loss)} perdus par grille en moyenne, soit ${EM.pct(average.loss / price, 0)} de la mise. ` +
        `Aucune stratégie n’y change rien${EM.NNBSP}: chaque grille de plus augmente la perte espérée, et le nombre de grilles ` +
        "qui maximise les gains espérés est zéro.")
      : tr("By design, the draw pays back about half of the stakes in prizes: each extra line adds to the expected loss, " +
        "so the number of lines that maximizes the expected winnings is zero.",
      `Par construction, le tirage reverse environ la moitié des mises en gains${EM.NNBSP}: chaque grille de plus augmente ` +
        "la perte espérée, et le nombre de grilles qui maximise les gains espérés est zéro.");
    const table = average ? el("div", { class: "scroll-x" }, el("table", { class: "data" },
      el("caption", {}, tr("Expected result, on average", "Résultat espéré, en moyenne")),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col" }, tr("Lines played at every draw", "Grilles jouées à chaque tirage")),
        el("th", { scope: "col" }, tr("Each draw", "Par tirage")),
        el("th", { scope: "col" }, tr(`Over a year (${DRAWS_A_YEAR} draws)`, `Sur un an (${DRAWS_A_YEAR} tirages)`)))),
      el("tbody", {}, [0, 1, 5, 10].map((n) => el("tr", {},
        el("th", { scope: "row" }, String(n)),
        el("td", {}, EM.euro(-n * average.loss)),
        el("td", {}, EM.euro(-n * average.loss * DRAWS_A_YEAR, 0))))))) : null;
    return el("section", { class: "card", "aria-labelledby": "optimumTitle", style: "gap: 16px" },
      el("div", { style: "display: flex; flex-direction: column; gap: 6px" },
        el("h2", { id: "optimumTitle" }, tr("Optimal: 0 lines", `Optimum${EM.NNBSP}: 0 grille`)),
        el("p", {}, why)),
      table,
      el("p", { class: "small muted" }, tr(
        "The prizes paid divided by the lines sold, counted from the winners of the last rank: jackpots included, " +
        "the My Million raffle left out. Type a number of lines or a budget to get lines anyway.",
        "Les gains versés divisés par les grilles vendues, comptées d’après les gagnants du dernier rang, jackpots compris, " +
        "hors tirage My Million. Saisissez un nombre de grilles ou un budget pour obtenir des grilles malgré tout.")));
  }

  function expect() {
    const average = averageLoss();
    const n = state.lines;
    const amounts = average
      ? [EM.euro(n * average.back), EM.euro(n * status.linePrice)]
      : null;
    const back = !amounts ? "" : n === 1
      ? tr(`On average, this line returns ${amounts[0]} in prizes for ${amounts[1]} staked. `,
        `En moyenne, cette grille rapporte ${amounts[0]} de gains pour ${amounts[1]} misés. `)
      : tr(`On average, these ${n} lines return ${amounts[0]} in prizes for ${amounts[1]} staked. `,
        `En moyenne, ces ${n} grilles rapportent ${amounts[0]} de gains pour ${amounts[1]} misés. `);
    let text = tr("In the backtest, every strategy loses about as much as random lines: none beats chance. Pick whichever lines you like.",
      `Au backtest, chaque stratégie perd à peu près autant que des grilles au hasard${EM.NNBSP}: aucune ne bat le hasard. Jouez les grilles qui vous plaisent.`);
    if (expectation) {
      const row = expectation.results.find((r) => r.strategy === state.strategy);
      const random = expectation.results.find((r) => r.strategy === "random");
      if (row) {
        const lost = (r) => EM.pct(-r.roi);
        const draws = EM.count(expectation.testDraws, "draw", "tirage");
        const chance = `${EM.pct(-expectation.chance.roi)} ±\u00a0${EM.points(expectation.chance.roiSd)}`;
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
        el("p", { class: "muted" }, back + text)),
      el("a", { class: "button", href: "backtest.html" }, tr("See the backtest", "Voir le backtest"), EM.icon("arrow")));
  }
})();
