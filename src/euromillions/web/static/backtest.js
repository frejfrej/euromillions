// The Backtest screen: play the strategies on past draws and read the outcome against chance.
"use strict";

(() => {
  const { el } = EM;
  const WINDOWS = [50, 100, 200];
  const POLL_MS = 500;
  let status = null;
  let container = null;
  let job = null;
  let settings = null;
  let poller = null;
  let startedAt = null;
  let formError = "";

  EM.start("backtest.html", async (current, target) => {
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

  // --- Running ---

  async function run() {
    formError = "";
    const seed = String(settings.seed).trim();
    if (seed && !/^-?\d+$/.test(seed)) {
      formError = "The seed must be a whole number, or left empty.";
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
        el("h1", {}, "Backtest the strategies"),
        el("p", {}, "Each past draw is played with lines suggested from the draws before it, then scored with the prizes " +
          "really paid that day, shared with that day’s winners.")),
      form(),
      el("div", { id: "job", style: "display: contents" }));
    renderJob();
  }

  function renderJob() {
    const box = document.getElementById("job");
    if (!box) return;
    const parts = [];
    if (job.state === "running") parts.push(progress());
    if (job.state === "cancelled") parts.push(el("p", { class: "notice" }, "The backtest was cancelled."));
    if (job.state === "failed") parts.push(el("p", { class: "notice error" }, `The backtest failed: ${job.error}`));
    if (job.state === "done") parts.push(...results(job.result, job.settings));
    box.replaceChildren(...parts);
    const button = document.getElementById("run");
    if (button) button.disabled = job.state === "running";
  }

  // --- Settings ---

  function trainHint() {
    const total = status.draws;
    const fraction = Number(settings.trainFraction);
    if (!(fraction > 0 && fraction < 1)) return "Between 0 and 1.";
    const train = Math.floor(total * fraction + 1e-9);
    return `The first ${EM.num(train)} draws build the statistics; the next ${EM.num(total - train)} are played.`;
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
      }, on ? EM.icon("check") : null, name === "random" ? "random (baseline)" : name);
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
    return el("section", { class: "card", "aria-label": "Settings" },
      el("div", { class: "row", style: "align-items: flex-end; gap: 18px 32px" },
        el("div", { class: "field", style: "flex: 2 1 420px", role: "group", "aria-labelledby": "strategiesLabel" },
          el("span", { class: "label", id: "strategiesLabel" }, "Strategies"),
          el("div", { class: "chips" }, all.map(chip))),
        el("div", { class: "field", style: "flex: 0 1 auto; min-width: 0", role: "group", "aria-labelledby": "statsLabel" },
          el("span", { class: "label", id: "statsLabel" }, "Statistics"),
          el("div", { class: "segmented" },
            segment(true, "Refreshed before each draw"), segment(false, "Frozen after training")))),
      el("div", { class: "row", style: "align-items: flex-end; gap: 18px 24px" },
        el("div", { class: "field", style: "flex: 2 1 280px" },
          el("label", { for: "train" }, "Training part"),
          el("div", { style: "display: flex; gap: 8px" },
            input("train", "trainFraction", { type: "number", min: min === null ? 0.01 : min, max: 0.99, step: 0.001, style: "flex: 1; min-width: 0", "aria-describedby": "trainHint" }),
            min === null ? null : el("button", {
              type: "button", class: "button",
              onclick: () => { settings.trainFraction = min; render(); },
            }, `Every draw since ${EM.date(status.currentRulesSince).replace(/^\d+ /, "")}`)),
          el("span", { class: "hint", id: "trainHint" }, trainHint()),
          min === null ? null : el("span", { class: "hint" },
            `${min} at least: only draws since ${EM.date(status.currentRulesSince)} can be played.`)),
        el("div", { class: "field", style: "flex: 0 1 120px" },
          el("label", { for: "lpd" }, "Lines a draw"), input("lpd", "lines", { type: "number", min: 1, max: 50 })),
        el("div", { class: "field", style: "flex: 0 1 150px" },
          el("label", { for: "bwindow" }, "Recent window"),
          el("select", { id: "bwindow", onchange: (event) => { settings.window = Number(event.target.value); } },
            WINDOWS.map((w) => el("option", { value: w, selected: w === Number(settings.window) }, `Last ${w} draws`)))),
        el("div", { class: "field", style: "flex: 0 1 120px" },
          el("label", { for: "bseed" }, "Seed"), input("bseed", "seed", { type: "text", inputmode: "numeric", placeholder: "random" })),
        el("div", { class: "field", style: "flex: 0 1 120px" },
          el("label", { for: "price" }, "Line price (€)"), input("price", "price", { type: "number", min: 0.01, step: 0.01 })),
        el("div", { style: "flex: 0 0 auto" },
          el("button", { id: "run", type: "button", class: "button primary", onclick: run }, EM.icon("play"), "Run backtest"))),
      formError ? el("p", { class: "notice error" }, formError) : null);
  }

  function progress() {
    const share = job.total ? job.done / job.total : 0;
    let left = "";
    if (startedAt && share > 0.02) {
      const seconds = Math.round(((Date.now() - startedAt) / 1000) * (1 - share) / share);
      left = seconds > 1 ? ` · about ${EM.plural(seconds, "second")} left` : " · almost done";
    }
    return el("div", { class: "progress", role: "status" },
      el("span", {}, `Running: ${EM.pct(share, 0)}${left}`),
      el("div", { class: "track", "aria-hidden": "true" }, el("div", { class: "fill", style: `width: ${(100 * share).toFixed(1)}%` })),
      el("button", { type: "button", class: "button", onclick: cancel }, "Cancel"));
  }

  // --- Results ---

  const label = (name) => (name === "random" ? "random (baseline)" : name);

  function headline(result) {
    const outside = result.results.filter((r) => !r.withinChance);
    const edges = result.results.filter((r) => r.edge);
    const threshold = result.reading.edgeThreshold.toFixed(1);
    if (!outside.length && !edges.length) {
      return [`Every strategy did what chance does: winnings within two standard deviations of random lines, and no match count beyond ±${threshold}.`];
    }
    const chance = `${EM.euro(result.chance.winnings)} ± ${EM.euro(result.chance.sd)}`;
    const lines = [
      ...outside.map((r) => `${EM.capitalize(label(r.strategy))} won ${EM.euro(r.commonWinnings)} in the common prize ranks, outside the ${chance} that random lines win by chance.`),
      ...edges.map((r) => `${EM.capitalize(label(r.strategy))} matched numbers and stars at z-scores of ${EM.signed(r.mainZ, 1)} and ${EM.signed(r.starZ, 1)}, beyond ±${threshold}.`),
    ];
    lines.push("With this many strategies and z-scores, chance puts one outside now and then: only a result that holds with other seeds and periods would mean something.");
    return lines;
  }

  function period(result, settingsRun) {
    const how = result.refresh
      ? `statistics refreshed before each draw, from the ${EM.plural(result.trainDraws, "draw")} up to ${EM.date(result.trainEnd)} at first`
      : `statistics frozen after the ${EM.plural(result.trainDraws, "draw")} up to ${EM.date(result.trainEnd)}`;
    const seed = settingsRun && settingsRun.seed !== null && settingsRun.seed !== undefined ? ` · seed ${settingsRun.seed}` : "";
    return `${EM.plural(result.testDraws, "draw")} played, from ${EM.date(result.testStart)} to ${EM.date(result.testEnd)} · ` +
      `${EM.plural(result.linesPerDraw, "line")} a draw · ${EM.euro(result.cost)} staked per strategy · ${how}${seed}`;
  }

  function tiles(result) {
    const best = result.results.reduce((a, b) => (b.roi > a.roi ? b : a));
    const tile = (name, value, note) => el("div", { class: "tile" },
      el("span", { class: "small muted" }, name), el("span", { class: "value" }, value),
      note ? el("span", { class: "small muted" }, note) : null);
    const ranks = result.reading.commonRanks;
    return el("div", { class: "stat-tiles" },
      tile("Staked per strategy", EM.euro(result.cost), EM.plural(result.lines, "line")),
      tile("Best return on stake", EM.signedPct(best.roi), label(best.strategy)),
      tile("Random lines, by chance", `${EM.signedPct(result.chance.roi)} ± ${EM.num(result.chance.roiSd * 100, 1)}`,
        `prize ranks ${ranks[0]} to ${ranks[ranks.length - 1]}`));
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
      title: `${label(r.strategy)}: ${EM.euro(r.commonWinnings)} in the common ranks` +
        (r.topWinnings ? `, plus ${EM.euro(r.topWinnings)} in the top ranks (luck)` : ""),
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
      el("h3", { style: "font-size: 16px" }, "Winnings of each strategy in the common prize ranks, against what random lines win by chance"),
      el("div", {
        class: "dotplot", role: "img",
        "aria-label": `Dot plot of the winnings: chance gives ${EM.euro(mean, 0)} ± ${EM.euro(sd, 0)}. ` +
          result.results.map((r) => `${label(r.strategy)} ${EM.euro(r.commonWinnings, 0)}`).join(", ") + ".",
      },
        rows,
        el("div", { class: "dot-row", style: "height: auto; padding-top: 6px" }, el("span", {}),
          el("div", { class: "axis" }, ticks.map((tick) => el("span", { style: `left: ${at(tick)}` }, EM.euro(tick, 0)))),
          el("span", {}))),
      el("div", { class: "legend" },
        el("span", {}, el("span", { class: "swatch", style: "background: var(--bar); border-radius: 50%" }), "Strategy"),
        el("span", {}, el("span", { class: "swatch", style: "background: #fff; border: 2px solid var(--ink); border-radius: 50%" }), "Random lines (baseline)"),
        el("span", {}, el("span", { class: "swatch", style: "background: var(--ink-2); width: 2px" }), `Average by chance, ${EM.euro(mean, 0)}`),
        el("span", {}, el("span", { class: "swatch", style: "background: var(--band-1); width: 18px" }), "Within 1 standard deviation"),
        el("span", {}, el("span", { class: "swatch", style: "background: var(--band-2); width: 18px; box-shadow: inset 0 0 0 1px #d6d4cc" }), "Within 2")),
      lucky.length
        ? el("p", { class: "small muted" }, lucky.map((r) => `${EM.capitalize(label(r.strategy))} also won ${EM.euro(r.topWinnings)} in the top ranks`).join("; ") +
          ": luck, left out of this comparison.")
        : null);
  }

  function table(result) {
    const matches = (perLine, z) => `${perLine.toFixed(3)} (${EM.signed(z, 1)})`;
    const head = ["Strategy", "Winnings", "Net", "Return on stake", "Winning lines", "Numbers a line (z)", "Stars a line (z)"];
    const c = result.chance;
    return el("div", { class: "scroll-x" }, el("table", { class: "data", style: "min-width: 820px" },
      el("caption", {}, "The figures"),
      el("thead", {}, el("tr", {}, head.map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {},
        result.results.map((r) => el("tr", {},
          el("th", { scope: "row" }, label(r.strategy)),
          el("td", {}, EM.euro(r.winnings)), el("td", {}, EM.euro(r.net)), el("td", {}, EM.signedPct(r.roi)),
          el("td", {}, EM.pct(r.winRate)), el("td", {}, matches(r.mainPerLine, r.mainZ)), el("td", {}, matches(r.starPerLine, r.starZ)))),
        el("tr", { class: "chance" },
          el("th", { scope: "row" }, "by chance"),
          el("td", {}, `${EM.euro(c.winnings)} ± ${EM.num(c.sd, 2)}`),
          el("td", {}, EM.euro(c.winnings - result.cost)),
          el("td", {}, `${EM.signedPct(c.roi)} ± ${EM.num(c.roiSd * 100, 1)}`),
          el("td", {}, `${EM.pct(c.winRate)} ± ${EM.num(c.winRateSd * 100, 1)}`),
          el("td", {}, c.mainPerLine.toFixed(3)), el("td", {}, c.starPerLine.toFixed(3))))));
  }

  function winsByRank(result) {
    const won = new Set();
    for (const r of result.results) {
      for (const [rank, wins] of Object.entries(r.winsByRank)) if (wins) won.add(Number(rank));
    }
    const common = result.reading.commonRanks;
    for (const rank of common.slice(-3)) won.add(rank);
    const ranks = [...won].sort((a, b) => b - a);
    const expected = (count) => (count >= 100 ? count.toFixed(0) : count.toFixed(1));
    return el("div", { class: "scroll-x" }, el("table", { class: "data" },
      el("caption", {}, "Winning lines by prize rank (numbers + stars matched)"),
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Strategy"),
        ranks.map((rank) => el("th", { scope: "col", title: `Rank ${rank}` }, result.rankMatches[rank].join("+"))))),
      el("tbody", {},
        result.results.map((r) => el("tr", {}, el("th", { scope: "row" }, label(r.strategy)),
          ranks.map((rank) => el("td", {}, r.winsByRank[rank] ? EM.num(r.winsByRank[rank]) : "–")))),
        el("tr", { class: "chance" }, el("th", { scope: "row" }, "by chance"),
          ranks.map((rank) => el("td", {}, expected(result.chance.wins[rank])))))));
  }

  function notes(result) {
    const reading = result.reading;
    const ranks = reading.commonRanks;
    const items = [
      `By chance: what random lines are expected to win on these draws from prize ranks ${ranks[0]} to ${ranks[ranks.length - 1]}, ` +
        "and by how much that varies (one standard deviation) for this many lines. A strategy within about two standard " +
        "deviations of it, the random one included, has done nothing that chance does not do.",
      `Numbers and stars a line: average matches per line, with the z-score against pure chance in brackets. Chance alone puts a ` +
        `z-score beyond ±2 about once in ${Math.round(1 / reading.zBeyondTwo)}: with ${reading.zScores} of them here, at least one in ` +
        `about ${EM.pct(reading.extremeZShare, 0)} of backtests. Only a value beyond about ±${reading.edgeThreshold.toFixed(1)} that ` +
        "holds with other seeds and periods would point to a real edge, and a small one could go unnoticed.",
      `The ${reading.topRanks} top ranks, jackpot included, hold about half of the prize money, but a line wins one of them ` +
        `about once in ${(reading.topRankOdds / 1e6).toFixed(1)} million times: far too rare for a backtest to measure.` +
        (reading.rafflePrice
          ? ` The cost includes ${EM.euro(reading.rafflePrice)} per line for the My Million raffle, which is not simulated; by design, ` +
            `the draw returns half of the other ${EM.euro(result.linePrice - reading.rafflePrice)} in prizes in the long run, top ranks included.`
          : ""),
    ];
    const lucky = result.results.filter((r) => r.topWins);
    if (lucky.length) {
      items.push("Top ranks won here: " + lucky.map((r) => `${label(r.strategy)}, ${EM.euro(r.topWinnings)} with ${EM.plural(r.topWins, "line")}`).join("; ") +
        ". The by-chance figures leave these ranks out: set that money aside before comparing, and read it as luck that every line had the same chance of.");
    }
    if (reading.fewLines) items.push(`With fewer than ${EM.num(reading.fewLinesThreshold)} lines per strategy, luck dominates these figures.`);
    if (reading.estimatedWins) items.push(`${EM.plural(reading.estimatedWins, "win")} valued by estimate: the rank had no winner that day, or their number is unknown.`);
    if (reading.skippedDraws) items.push(`${EM.plural(reading.skippedDraws, "draw")} without prize data could not be played.`);
    if (result.bestWin) {
      const b = result.bestWin;
      items.push(`Biggest win: ${EM.euro(b.amount)} by ${label(b.strategy)} on ${EM.date(b.date)} (${EM.plural(b.numbers, "number")} + ${EM.plural(b.stars, "star")}).`);
    }
    items.push(`Only draws since ${EM.date(status.currentRulesSince)} can be played: earlier ones had other star pools and prize ranks.`);
    return el("section", { class: "card", "aria-labelledby": "readTitle", style: "gap: 12px" },
      el("h2", { id: "readTitle", style: "font-size: 20px" }, "How to read this"),
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
