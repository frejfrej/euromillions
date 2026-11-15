// The Backtest screen: play the strategies on past draws and read the outcome against chance.
"use strict";

(() => {
  const { el, tr } = EM;
  const WINDOWS = [50, 100, 200];
  const POLL_MS = 500;
  let status = null;
  let container = null;
  let job = null;
  let settings = null;
  let poller = null;
  let startedAt = null;
  let formError = "";

  EM.start("backtest.html", ["Backtest", "Backtest"], async (current, target) => {
    status = current;
    container = target;
    if (!settings) {
      settings = {
        strategies: [...status.strategies.map((s) => s.name), "random"],
        trainFraction: 0.8,
        lines: 5,
        window: 50,
        seed: "",
        refresh: true,
        price: status.linePrice,
      };
    }
    job = await EM.get("/api/backtest");
    if (job.state === "running") watch();
    render();
  });

  const label = (name) => EM.strategyName(name);
  const draws = (n) => EM.count(n, "draw", "tirage");

  // --- Running ---

  async function run() {
    formError = "";
    const seed = String(settings.seed).trim();
    if (seed && !/^-?\d+$/.test(seed)) {
      formError = tr("The seed must be a whole number, or left empty.", "La graine doit être un nombre entier, ou rester vide.");
      render();
      return;
    }
    const body = {
      strategies: settings.strategies,
      trainFraction: Number(settings.trainFraction),
      lines: Number(settings.lines),
      window: Number(settings.window),
      seed: seed ? Number(seed) : null,
      refresh: settings.refresh,
      price: Number(settings.price),
    };
    try {
      job = await EM.send("POST", "/api/backtest", body);
      startedAt = Date.now();
      watch();
    } catch (failure) {
      formError = failure.message;
    }
    render();
  }

  function watch() {
    clearTimeout(poller);
    poller = setTimeout(async () => {
      try {
        job = await EM.get("/api/backtest");
      } catch (failure) {
        formError = failure.message;
        render();
        return;
      }
      if (job.state === "running") watch();
      renderJob();
    }, POLL_MS);
  }

  async function cancel() {
    try {
      job = await EM.send("DELETE", "/api/backtest");
    } catch (failure) {
      formError = failure.message;
    }
    clearTimeout(poller);
    render();
  }

  // --- Layout ---

  function render() {
    container.replaceChildren(
      el("div", { class: "intro" },
        el("h1", {}, tr("Backtest the strategies", "Tester les stratégies sur le passé")),
        el("p", {}, tr("Each past draw is played with lines suggested from the draws before it, then scored with the prizes " +
          "really paid that day, shared with that day’s winners.",
        "Chaque tirage passé est joué avec des grilles suggérées d’après les tirages précédents, puis noté avec les gains " +
          "réellement versés ce jour-là, partagés avec les gagnants du jour."))),
      form(),
      el("div", { id: "job", style: "display: contents" }));
    renderJob();
  }

  function renderJob() {
    const box = document.getElementById("job");
    if (!box) return;
    const parts = [];
    if (job.state === "running") parts.push(progress());
    if (job.state === "cancelled") parts.push(el("p", { class: "notice" }, tr("The backtest was cancelled.", "Le backtest a été annulé.")));
    if (job.state === "failed") {
      parts.push(el("p", { class: "notice error" }, tr(`The backtest failed: ${job.error}`, `Le backtest a échoué${EM.NNBSP}: ${job.error}`)));
    }
    if (job.state === "done") parts.push(...results(job.result, job.settings));
    box.replaceChildren(...parts);
    const button = document.getElementById("run");
    if (button) button.disabled = job.state === "running";
  }

  // --- Settings ---

  function trainHint() {
    const total = status.draws;
    const fraction = Number(settings.trainFraction);
    if (!(fraction > 0 && fraction < 1)) return tr("Between 0 and 1.", "Entre 0 et 1.");
    const train = Math.floor(total * fraction + 1e-9);
    return tr(`The first ${EM.num(train)} draws build the statistics; the next ${EM.num(total - train)} are played.`,
      `Les ${EM.num(train)} premiers tirages servent aux statistiques${EM.NNBSP}; les ${EM.num(total - train)} suivants sont joués.`);
  }

  function form() {
    const all = [...status.strategies.map((s) => s.name), "random"];
    const chip = (name) => {
      const on = settings.strategies.includes(name);
      return el("button", {
        type: "button", class: "chip-toggle", "aria-pressed": String(on),
        onclick: () => {
          settings.strategies = on ? settings.strategies.filter((s) => s !== name) : all.filter((s) => s === name || settings.strategies.includes(s));
          render();
        },
      }, on ? EM.icon("check") : null, label(name));
    };
    const segment = (refresh, text) => el("button", {
      type: "button", "aria-pressed": String(settings.refresh === refresh),
      onclick: () => { settings.refresh = refresh; render(); },
    }, text);
    const input = (id, key, attrs) => el("input", {
      id, value: settings[key], ...attrs,
      oninput: (event) => {
        settings[key] = event.target.value;
        if (key === "trainFraction") document.getElementById("trainHint").textContent = trainHint();
      },
    });
    const min = status.minTrainFraction;
    const since = status.currentRulesSince;
    return el("section", { class: "card", "aria-label": tr("Settings", "Réglages") },
      el("div", { class: "row", style: "align-items: flex-end; gap: 18px 32px" },
        el("div", { class: "field", style: "flex: 2 1 420px", role: "group", "aria-labelledby": "strategiesLabel" },
          el("span", { class: "label", id: "strategiesLabel" }, tr("Strategies", "Stratégies")),
          el("div", { class: "chips" }, all.map(chip))),
        el("div", { class: "field", style: "flex: 0 1 auto; min-width: 0", role: "group", "aria-labelledby": "statsLabel" },
          el("span", { class: "label", id: "statsLabel" }, tr("Statistics", "Statistiques")),
          el("div", { class: "segmented" },
            segment(true, tr("Refreshed before each draw", "Recalculées avant chaque tirage")),
            segment(false, tr("Frozen after training", "Figées après l’entraînement"))))),
      el("div", { class: "row", style: "align-items: flex-end; gap: 18px 24px" },
        el("div", { class: "field", style: "flex: 2 1 280px" },
          el("label", { for: "train" }, tr("Training part", "Part d’entraînement")),
          el("div", { style: "display: flex; gap: 8px" },
            input("train", "trainFraction", { type: "number", min: min === null ? 0.01 : min, max: 0.99, step: 0.001, style: "flex: 1; min-width: 0", "aria-describedby": "trainHint" }),
            min === null ? null : el("button", {
              type: "button", class: "button",
              onclick: () => { settings.trainFraction = min; render(); },
            }, tr(`Every draw since ${EM.monthYear(since)}`, `Tous les tirages depuis ${EM.monthYear(since)}`))),
          el("span", { class: "hint", id: "trainHint" }, trainHint()),
          min === null ? null : el("span", { class: "hint" }, tr(
            `${min} at least: only draws since ${EM.date(since)} can be played.`,
            `${EM.fixed(min, 3)} au moins${EM.NNBSP}: seuls les tirages depuis le ${EM.date(since)} peuvent être joués.`))),
        el("div", { class: "field", style: "flex: 0 1 120px" },
          el("label", { for: "lpd" }, tr("Lines a draw", "Grilles par tirage")), input("lpd", "lines", { type: "number", min: 1, max: 50 })),
        el("div", { class: "field", style: "flex: 0 1 150px" },
          el("label", { for: "bwindow" }, tr("Recent window", "Période récente")),
          el("select", { id: "bwindow", onchange: (event) => { settings.window = Number(event.target.value); } },
            WINDOWS.map((w) => el("option", { value: w, selected: w === Number(settings.window) }, tr(`Last ${w} draws`, `${w} derniers tirages`))))),
        el("div", { class: "field", style: "flex: 0 1 120px" },
          el("label", { for: "bseed" }, tr("Seed", "Graine")),
          input("bseed", "seed", { type: "text", inputmode: "numeric", placeholder: tr("random", "au hasard") })),
        el("div", { class: "field", style: "flex: 0 1 120px" },
          el("label", { for: "price" }, tr("Line price (€)", "Prix d’une grille (€)")), input("price", "price", { type: "number", min: 0.01, step: 0.01 })),
        el("div", { style: "flex: 0 0 auto" },
          el("button", { id: "run", type: "button", class: "button primary", onclick: run }, EM.icon("play"), tr("Run backtest", "Lancer le backtest")))),
      formError ? el("p", { class: "notice error" }, formError) : null);
  }

  function progress() {
    const share = job.total ? job.done / job.total : 0;
    let left = "";
    if (startedAt && share > 0.02) {
      const seconds = Math.round(((Date.now() - startedAt) / 1000) * (1 - share) / share);
      left = seconds > 1
        ? tr(` · about ${EM.count(seconds, "second")} left`, ` · encore environ ${EM.count(seconds, "second", "seconde")}`)
        : tr(" · almost done", " · presque fini");
    }
    return el("div", { class: "progress", role: "status" },
      el("span", {}, tr(`Running: ${EM.pct(share, 0)}${left}`, `En cours${EM.NNBSP}: ${EM.pct(share, 0)}${left}`)),
      el("div", { class: "track", "aria-hidden": "true" }, el("div", { class: "fill", style: `width: ${(100 * share).toFixed(1)}%` })),
      el("button", { type: "button", class: "button", onclick: cancel }, tr("Cancel", "Annuler")));
  }

  // --- Results ---

  function headline(result) {
    const outside = result.results.filter((r) => !r.withinChance);
    const edges = result.results.filter((r) => r.edge);
    const threshold = EM.fixed(result.reading.edgeThreshold);
    if (!outside.length && !edges.length) {
      return [tr(`Every strategy did what chance does: winnings within two standard deviations of random lines, and no match count beyond ±${threshold}.`,
        `Chaque stratégie a fait ce que fait le hasard${EM.NNBSP}: des gains à moins de deux écarts-types de ceux de grilles au hasard, et aucun score de numéros trouvés au-delà de ±${threshold}.`)];
    }
    const band = `${EM.euro(result.chance.winnings)} ± ${EM.euro(2 * result.chance.sd)}`;
    const lines = [
      ...outside.map((r) => tr(
        `${EM.capitalize(label(r.strategy))} won ${EM.euro(r.commonWinnings)} in the common prize ranks, outside the ${band} (two standard deviations) that random lines win by chance.`,
        `${EM.capitalize(label(r.strategy))} a gagné ${EM.euro(r.commonWinnings)} dans les rangs courants, hors des ${band} (deux écarts-types) que gagnent des grilles au hasard.`)),
      ...edges.map((r) => tr(
        `${EM.capitalize(label(r.strategy))} matched numbers and stars at z-scores of ${EM.signed(r.mainZ)} and ${EM.signed(r.starZ)}, beyond ±${threshold}.`,
        `${EM.capitalize(label(r.strategy))} a trouvé numéros et étoiles avec des scores z de ${EM.signed(r.mainZ)} et ${EM.signed(r.starZ)}, au-delà de ±${threshold}.`)),
    ];
    lines.push(tr("With this many strategies and z-scores, chance puts one outside now and then: only a result that holds with other seeds and periods would mean something.",
      `Avec autant de stratégies et de scores z, le hasard en place parfois un en dehors${EM.NNBSP}: seul un résultat qui tient avec d’autres graines et d’autres périodes voudrait dire quelque chose.`));
    return lines;
  }

  function period(result, settingsRun) {
    const how = result.refresh
      ? tr(`statistics refreshed before each draw, from the ${draws(result.trainDraws)} up to ${EM.date(result.trainEnd)} at first`,
        `statistiques recalculées avant chaque tirage, d’abord sur les ${draws(result.trainDraws)} jusqu’au ${EM.date(result.trainEnd)}`)
      : tr(`statistics frozen after the ${draws(result.trainDraws)} up to ${EM.date(result.trainEnd)}`,
        `statistiques figées après les ${draws(result.trainDraws)} jusqu’au ${EM.date(result.trainEnd)}`);
    const seeded = settingsRun && settingsRun.seed !== null && settingsRun.seed !== undefined;
    const seed = seeded ? tr(` · seed ${settingsRun.seed}`, ` · graine ${settingsRun.seed}`) : "";
    return tr(
      `${draws(result.testDraws)} played, from ${EM.date(result.testStart)} to ${EM.date(result.testEnd)} · ` +
      `${EM.count(result.linesPerDraw, "line")} a draw · ${EM.euro(result.cost)} staked per strategy · ${how}${seed}`,
      `${draws(result.testDraws)} ${result.testDraws > 1 ? "joués" : "joué"}, du ${EM.date(result.testStart)} au ${EM.date(result.testEnd)} · ` +
      `${EM.count(result.linesPerDraw, "line", "grille")} par tirage · ${EM.euro(result.cost)} misés par stratégie · ${how}${seed}`);
  }

  function tiles(result) {
    const within = result.results.filter((r) => r.withinChance).length;
    const tile = (name, value, note) => el("div", { class: "tile" },
      el("span", { class: "small muted" }, name), el("span", { class: "value" }, value),
      note ? el("span", { class: "small muted" }, note) : null);
    const ranks = result.reading.commonRanks;
    return el("div", { class: "stat-tiles" },
      tile(tr("Staked per strategy", "Mise par stratégie"), EM.euro(result.cost), EM.count(result.lines, "line", "grille")),
      // No ranking of the strategies: how many did what chance does.
      tile(tr("Within chance", "Dans le hasard"), tr(`${within} of ${result.results.length}`, `${within} sur ${result.results.length}`),
        tr("winnings within two standard deviations", "gains à moins de deux écarts-types")),
      tile(tr("Random lines, by chance", "Grilles au hasard, en moyenne"),
        `${EM.signedPct(result.chance.roi)} ± ${EM.points(result.chance.roiSd)}`,
        tr(`prize ranks ${ranks[0]} to ${ranks[ranks.length - 1]}`, `rangs ${ranks[0]} à ${ranks[ranks.length - 1]}`)));
  }

  function niceStep(span) {
    const raw = span / 6;
    const power = 10 ** Math.floor(Math.log10(raw));
    return [1, 2, 2.5, 5, 10].map((m) => m * power).find((step) => step >= raw);
  }

  function dotplot(result) {
    const mean = result.chance.winnings;
    const sd = result.chance.sd;
    const values = result.results.map((r) => r.commonWinnings);
    let low = Math.min(mean - 2.5 * sd, ...values);
    let high = Math.max(mean + 2.5 * sd, ...values);
    const pad = (high - low) * 0.04 || 1;
    low -= pad;
    high += pad;
    const at = (value) => `${(100 * (value - low) / (high - low)).toFixed(2)}%`;
    const width = (from, to) => `${(100 * (to - from) / (high - low)).toFixed(2)}%`;
    const step = niceStep(high - low);
    const ticks = [];
    for (let tick = Math.ceil(low / step) * step; tick <= high; tick += step) ticks.push(tick);
    const rows = result.results.map((r) => el("div", {
      class: "dot-row",
      title: tr(`${label(r.strategy)}: ${EM.euro(r.commonWinnings)} in the common ranks`,
        `${label(r.strategy)}${EM.NNBSP}: ${EM.euro(r.commonWinnings)} dans les rangs courants`) +
        (r.topWinnings ? tr(`, plus ${EM.euro(r.topWinnings)} in the top ranks (luck)`, `, plus ${EM.euro(r.topWinnings)} dans les premiers rangs (chance)`) : ""),
    },
      el("span", { class: "label" }, label(r.strategy)),
      el("div", { class: "plot" },
        el("div", { class: "sd2", style: `left: ${at(mean - 2 * sd)}; width: ${width(mean - 2 * sd, mean + 2 * sd)}` }),
        el("div", { class: "sd1", style: `left: ${at(mean - sd)}; width: ${width(mean - sd, mean + sd)}` }),
        el("div", { class: "mean", style: `left: calc(${at(mean)} - 1px)` }),
        el("div", { class: r.strategy === "random" ? "dot baseline" : "dot", style: `left: ${at(r.commonWinnings)}` })),
      el("span", { class: "value" }, EM.euro(r.commonWinnings, 0))));
    const lucky = result.results.filter((r) => r.topWinnings);
    return el("div", { style: "display: flex; flex-direction: column; gap: 10px" },
      el("h3", { style: "font-size: 16px" }, tr("Winnings of each strategy in the common prize ranks, against what random lines win by chance",
        "Gains de chaque stratégie dans les rangs courants, face à ce que gagnent des grilles au hasard")),
      el("div", {
        class: "dotplot", role: "img",
        "aria-label": tr(`Dot plot of the winnings: chance gives ${EM.euro(mean, 0)} ± ${EM.euro(sd, 0)}. `,
          `Graphique des gains${EM.NNBSP}: le hasard donne ${EM.euro(mean, 0)} ± ${EM.euro(sd, 0)}. `) +
          result.results.map((r) => `${label(r.strategy)} ${EM.euro(r.commonWinnings, 0)}`).join(", ") + ".",
      },
        rows,
        el("div", { class: "dot-row", style: "height: auto; padding-top: 6px" }, el("span", {}),
          el("div", { class: "axis" }, ticks.map((tick) => el("span", { style: `left: ${at(tick)}` }, EM.euro(tick, 0)))),
          el("span", {}))),
      el("div", { class: "legend" },
        el("span", {}, el("span", { class: "swatch", style: "background: var(--bar); border-radius: 50%" }), tr("Strategy", "Stratégie")),
        el("span", {}, el("span", { class: "swatch", style: "background: #fff; border: 2px solid var(--ink); border-radius: 50%" }),
          tr("Random lines (baseline)", "Grilles au hasard (référence)")),
        el("span", {}, el("span", { class: "swatch", style: "background: var(--ink-2); width: 2px" }),
          tr(`Average by chance, ${EM.euro(mean, 0)}`, `Moyenne du hasard, ${EM.euro(mean, 0)}`)),
        el("span", {}, el("span", { class: "swatch", style: "background: var(--band-1); width: 18px" }),
          tr("Within 1 standard deviation", "À moins d’un écart-type")),
        el("span", {}, el("span", { class: "swatch", style: "background: var(--band-2); width: 18px; box-shadow: inset 0 0 0 1px #d6d4cc" }),
          tr("Within 2", "À moins de deux"))),
      lucky.length
        ? el("p", { class: "small muted" }, lucky.map((r) => tr(`${EM.capitalize(label(r.strategy))} also won ${EM.euro(r.topWinnings)} in the top ranks`,
          `${EM.capitalize(label(r.strategy))} a aussi gagné ${EM.euro(r.topWinnings)} dans les premiers rangs`)).join("; ") +
          tr(": luck, left out of this comparison.", `${EM.NNBSP}: de la chance, laissée hors de cette comparaison.`))
        : null);
  }

  function table(result) {
    const matches = (perLine, z) => `${EM.fixed(perLine, 3)} (${EM.signed(z, 1)})`;
    const head = [tr("Strategy", "Stratégie"), tr("Winnings", "Gains"), tr("Net", "Net"), tr("Return on stake", "Rendement"),
      tr("Winning lines", "Grilles gagnantes"), tr("Numbers a line (z)", "Numéros par grille (z)"), tr("Stars a line (z)", "Étoiles par grille (z)")];
    const c = result.chance;
    return el("div", { class: "scroll-x" }, el("table", { class: "data", style: "min-width: 820px" },
      el("caption", {}, tr("The figures", "Les chiffres")),
      el("thead", {}, el("tr", {}, head.map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {},
        result.results.map((r) => el("tr", {},
          el("th", { scope: "row" }, label(r.strategy)),
          el("td", {}, EM.euro(r.winnings)), el("td", {}, EM.euro(r.net)), el("td", {}, EM.signedPct(r.roi)),
          el("td", {}, EM.pct(r.winRate)), el("td", {}, matches(r.mainPerLine, r.mainZ)), el("td", {}, matches(r.starPerLine, r.starZ)))),
        el("tr", { class: "chance" },
          el("th", { scope: "row" }, tr("by chance", "le hasard")),
          el("td", {}, `${EM.euro(c.winnings)} ± ${EM.euro(c.sd)}`),
          el("td", {}, EM.euro(c.winnings - result.cost)),
          el("td", {}, `${EM.signedPct(c.roi)} ± ${EM.points(c.roiSd)}`),
          el("td", {}, `${EM.pct(c.winRate)} ± ${EM.points(c.winRateSd)}`),
          el("td", {}, EM.fixed(c.mainPerLine, 3)), el("td", {}, EM.fixed(c.starPerLine, 3))))));
  }

  function winsByRank(result) {
    const won = new Set();
    for (const r of result.results) {
      for (const [rank, wins] of Object.entries(r.winsByRank)) if (wins) won.add(Number(rank));
    }
    const common = result.reading.commonRanks;
    for (const rank of common.slice(-3)) won.add(rank);
    const ranks = [...won].sort((a, b) => b - a);
    const expected = (n) => (n >= 100 ? EM.fixed(n, 0) : EM.fixed(n, 1));
    return el("div", { class: "scroll-x" }, el("table", { class: "data" },
      el("caption", {}, tr("Winning lines by prize rank (numbers + stars matched)", "Grilles gagnantes par rang (numéros + étoiles trouvés)")),
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, tr("Strategy", "Stratégie")),
        ranks.map((rank) => el("th", { scope: "col", title: tr(`Rank ${rank}`, `Rang ${rank}`) }, result.rankMatches[rank].join("+"))))),
      el("tbody", {},
        result.results.map((r) => el("tr", {}, el("th", { scope: "row" }, label(r.strategy)),
          ranks.map((rank) => el("td", {}, r.winsByRank[rank] ? EM.num(r.winsByRank[rank]) : "–")))),
        el("tr", { class: "chance" }, el("th", { scope: "row" }, tr("by chance", "le hasard")),
          ranks.map((rank) => el("td", {}, expected(result.chance.wins[rank])))))));
  }

  function notes(result) {
    const reading = result.reading;
    const ranks = reading.commonRanks;
    const first = ranks[0];
    const last = ranks[ranks.length - 1];
    const once = Math.round(1 / reading.zBeyondTwo);
    const share = EM.pct(reading.extremeZShare, 0);
    const edge = EM.fixed(reading.edgeThreshold);
    const odds = EM.fixed(reading.topRankOdds / 1e6);
    const items = [
      tr(`By chance: what random lines are expected to win on these draws from prize ranks ${first} to ${last}, ` +
        "and by how much that varies (one standard deviation) for this many lines. A strategy within about two standard " +
        "deviations of it, the random one included, has done nothing that chance does not do.",
      `Le hasard${EM.NNBSP}: ce que des grilles au hasard devaient gagner sur ces tirages dans les rangs ${first} à ${last}, ` +
        "et de combien cela varie (un écart-type) pour autant de grilles. Une stratégie à moins de deux écarts-types environ, " +
        "la stratégie aléatoire comprise, n’a rien fait que le hasard ne fasse."),
      tr(`Numbers and stars a line: average matches per line, with the z-score against pure chance in brackets. Chance alone puts a ` +
        `z-score beyond ±2 about once in ${once}: with ${reading.zScores} of them here, at least one in about ${share} of backtests. ` +
        `Only a value beyond about ±${edge} that holds with other seeds and periods would point to a real edge, and a small one could go unnoticed.`,
      `Numéros et étoiles par grille${EM.NNBSP}: nombre moyen trouvé par grille, avec entre parenthèses le score z face au hasard. Le hasard seul ` +
        `place un score z au-delà de ±2 environ une fois sur ${once}${EM.NNBSP}: avec ${reading.zScores} scores ici, au moins un dans environ ${share} des backtests. ` +
        `Seule une valeur au-delà de ±${edge} environ, qui tient avec d’autres graines et d’autres périodes, indiquerait un vrai avantage, et un petit avantage pourrait passer inaperçu.`),
      tr(`The ${reading.topRanks} top ranks, jackpot included, hold about half of the prize money, but a line wins one of them ` +
        `about once in ${odds} million times: far too rare for a backtest to measure.`,
      `Les ${reading.topRanks} premiers rangs, jackpot compris, concentrent environ la moitié des gains, mais une grille n’en gagne un ` +
        `qu’environ une fois sur ${odds} millions${EM.NNBSP}: bien trop rare pour qu’un backtest le mesure.`) +
        (reading.rafflePrice
          ? tr(` The cost includes ${EM.euro(reading.rafflePrice)} per line for the My Million raffle, which is not simulated; by design, ` +
            `the draw returns half of the other ${EM.euro(result.linePrice - reading.rafflePrice)} in prizes in the long run, top ranks included.`,
          ` Le coût comprend ${EM.euro(reading.rafflePrice)} par grille pour le tirage My Million, qui n’est pas simulé${EM.NNBSP}; par construction, ` +
            `le tirage reverse à long terme la moitié des ${EM.euro(result.linePrice - reading.rafflePrice)} restants en gains, premiers rangs compris.`)
          : ""),
    ];
    const lucky = result.results.filter((r) => r.topWins);
    if (lucky.length) {
      const list = lucky.map((r) => `${label(r.strategy)}, ${EM.euro(r.topWinnings)} ${tr("with", "avec")} ${EM.count(r.topWins, "line", "grille")}`).join("; ");
      items.push(tr(`Top ranks won here: ${list}. The by-chance figures leave these ranks out: set that money aside before comparing, ` +
        "and read it as luck that every line had the same chance of.",
      `Premiers rangs gagnés ici${EM.NNBSP}: ${list}. Les chiffres du hasard laissent ces rangs de côté${EM.NNBSP}: mettez cet argent à part avant de comparer, ` +
        "c’est une chance que chaque grille avait au même titre."));
    }
    if (reading.fewLines) {
      items.push(tr(`With fewer than ${EM.num(reading.fewLinesThreshold)} lines per strategy, luck dominates these figures.`,
        `Avec moins de ${EM.num(reading.fewLinesThreshold)} grilles par stratégie, la chance domine ces chiffres.`));
    }
    if (reading.estimatedWins) {
      items.push(tr(`${EM.count(reading.estimatedWins, "win")} valued by estimate: the rank had no winner that day, or their number is unknown.`,
        `${EM.count(reading.estimatedWins, "gain")} ${reading.estimatedWins > 1 ? "estimés" : "estimé"}${EM.NNBSP}: le rang n’avait pas de gagnant ce jour-là, ou leur nombre est inconnu.`));
    }
    if (reading.skippedDraws) {
      items.push(tr(`${draws(reading.skippedDraws)} without prize data could not be played.`,
        `${draws(reading.skippedDraws)} sans données de gains n’${reading.skippedDraws > 1 ? "ont" : "a"} pas pu être ${reading.skippedDraws > 1 ? "joués" : "joué"}.`));
    }
    if (result.bestWin) {
      const b = result.bestWin;
      items.push(tr(`Biggest win: ${EM.euro(b.amount)} by ${label(b.strategy)} on ${EM.date(b.date)} (${EM.count(b.numbers, "number")} + ${EM.count(b.stars, "star")}).`,
        `Plus gros gain${EM.NNBSP}: ${EM.euro(b.amount)} pour ${label(b.strategy)} le ${EM.date(b.date)} (${EM.count(b.numbers, "number", "numéro")} + ${EM.count(b.stars, "star", "étoile")}).`));
    }
    items.push(tr(`Only draws since ${EM.date(status.currentRulesSince)} can be played: earlier ones had other star pools and prize ranks.`,
      `Seuls les tirages depuis le ${EM.date(status.currentRulesSince)} peuvent être joués${EM.NNBSP}: les précédents avaient d’autres étoiles et d’autres rangs.`));
    return el("section", { class: "card", "aria-labelledby": "readTitle", style: "gap: 12px" },
      el("h2", { id: "readTitle", style: "font-size: 20px" }, tr("How to read this", "Comment lire ces résultats")),
      el("ul", { class: "reading" }, items.map((item) => el("li", {}, item))));
  }

  function results(result, settingsRun) {
    return [
      el("section", { class: "card", "aria-labelledby": "resultTitle", style: "gap: 24px" },
        el("div", { style: "display: flex; flex-direction: column; gap: 6px" },
          headline(result).map((text, i) => (i === 0
            ? el("h2", { id: "resultTitle", style: "font-size: 24px" }, text)
            : el("p", { style: "font-size: 16px" }, text))),
          el("p", { class: "muted" }, period(result, settingsRun))),
        tiles(result), dotplot(result), table(result), winsByRank(result)),
      notes(result),
    ];
  }
})();
