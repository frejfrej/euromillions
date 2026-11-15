// The Patterns screen: how the five main numbers of a draw sit together.
"use strict";

(() => {
  const { el } = EM;

  EM.start("patterns.html", async (status, container) => {
    const analysis = await EM.get("/api/analysis");
    container.replaceChildren(
      el("div", { class: "intro" },
        el("h1", {}, "Patterns of the five main numbers"),
        el("p", {}, `How the numbers of a draw sit together, over all ${EM.num(analysis.totalDraws)} draws. ` +
          "Where fair draws give an exact figure, it is shown next to what happened.")),
      EM.disclaimer(),
      sums(analysis),
      el("div", { class: "cards", style: "grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr)); gap: 24px" },
        split(analysis, "Odd and even", "How many of the five numbers are odd", analysis.oddCounts, ["odd", "even"]),
        split(analysis, "Low and high", `Low is 1 to ${analysis.lowMax}, high is ${analysis.lowMax + 1} to 50`,
          analysis.lowCounts, ["low", "high"])),
      el("div", { class: "cards", style: "grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr)); gap: 24px" },
        consecutive(analysis), range(analysis)),
      together(analysis));
  });

  const tile = (label, value, note) => el("div", { class: "tile" },
    el("span", { class: "small muted" }, label), el("span", { class: "value" }, value),
    note ? el("span", { class: "small muted" }, note) : null);

  // A histogram of ``bands`` of ``step`` values, with the central share of ``dist`` marked.
  function histogram(bands, step, dist, total, what, height) {
    const peak = Math.max(...bands.map((band) => band.draws));
    const room = (100 * (height - 28)) / height; // the tallest bar leaves room for the label
    const index = (value) => bands.findIndex((band) => value < band.from + step);
    const first = index(dist.low) + 1;
    const last = index(dist.high) + 1;
    const largest = bands.reduce((best, band) => (band.draws > best.draws ? band : best));
    const every = Math.ceil(bands.length / 10);
    return el("div", {
      class: "histogram", role: "img",
      style: `grid-template-columns: repeat(${bands.length}, minmax(0, 1fr)); grid-template-rows: ${height}px auto`,
      "aria-label": `Histogram of ${what}: ${EM.pct(dist.centralShare, 0)} of draws between ${dist.low} and ` +
        `${dist.high}; the most common band is ${largest.from} to ${largest.from + step - 1}, with ${EM.plural(largest.draws, "draw")}.`,
    },
      el("div", { class: "band", style: `grid-column: ${first} / ${last + 1}` }, `${EM.pct(dist.centralShare, 0)} of draws`),
      bands.map((band, i) => el("div", {
        class: "bar", title: `${band.from}–${band.from + step - 1}: ${EM.plural(band.draws, "draw")} (${EM.pct(band.draws / total)})`,
        style: `grid-column: ${i + 1}; height: ${(room * band.draws / peak).toFixed(1)}%`,
      })),
      el("div", { class: "baseline" }),
      bands.map((band, i) => el("span", { class: "tick", style: `grid-column: ${i + 1}` }, i % every ? "" : band.from)));
  }

  function sums(analysis) {
    const sums = analysis.sums;
    return el("section", { class: "card", "aria-labelledby": "sumTitle" },
      el("div", { class: "card-head" },
        el("div", {}, el("h2", { id: "sumTitle" }, "Sum of the five numbers"),
          el("p", { class: "muted" }, "Draws per band of 10"))),
      el("div", { class: "stat-tiles" },
        tile("Mean", sums.mean.toFixed(1), `fair draws: ${sums.fairMean.toFixed(1)}`),
        tile("Median", String(sums.median)),
        tile(`${EM.pct(sums.centralShare, 0)} of draws`, `${sums.low} – ${sums.high}`),
        tile("Lowest · highest", `${sums.min} · ${sums.max}`, `standard deviation ${sums.stdev.toFixed(1)}`)),
      histogram(analysis.sumBands, 10, sums, analysis.totalDraws, "the sum of the five main numbers", 220));
  }

  function split(analysis, title, sub, counts, [yes, no]) {
    const total = analysis.totalDraws;
    const rows = [5, 4, 3, 2, 1, 0].map((k) => ({
      label: `${k} ${yes} · ${5 - k} ${no}`,
      share: (counts[k] || 0) / total,
      count: counts[k] || 0,
      fair: analysis.fairSplit[k],
    }));
    const scale = Math.ceil(Math.max(...rows.map((r) => Math.max(r.share, r.fair))) * 10) / 10;
    return el("section", { class: "card" },
      el("div", { style: "display: flex; flex-direction: column; gap: 4px" },
        el("h2", { style: "font-size: 20px" }, title), el("p", { class: "small muted" }, sub)),
      el("div", { class: "hbars" }, rows.map((row) => el("div", {
        class: "hbar",
        title: `${row.label}: ${EM.plural(row.count, "draw")} (${EM.pct(row.share)}), fair draws ${EM.pct(row.fair)}`,
      },
        el("span", { class: "small" }, row.label),
        el("div", { class: "track", "aria-hidden": "true" },
          el("div", { class: "fill", style: `width: ${(100 * row.share / scale).toFixed(1)}%` }),
          el("div", { class: "mark", style: `left: calc(${(100 * row.fair / scale).toFixed(1)}% - 1px)` })),
        el("span", { class: "small num", style: "font-weight: 650; text-align: right" }, EM.pct(row.share))))),
      el("div", { class: "legend" },
        el("span", {}, el("span", { class: "swatch", style: "background: var(--bar); height: 10px" }), "Share of draws"),
        el("span", {}, el("span", { class: "swatch", style: "background: var(--ink); width: 2px" }), "Fair draws")));
  }

  function consecutive(analysis) {
    const c = analysis.consecutive;
    const names = ["No neighbours", "One pair", "Two pairs", "Three pairs", "Four pairs"];
    const share = (count) => (count && count / c.draws < 0.001 ? "under 0.1%" : EM.pct(count / c.draws));
    return el("section", { class: "card", "aria-labelledby": "consTitle" },
      el("h2", { id: "consTitle", style: "font-size: 20px" }, "Consecutive numbers"),
      el("div", { style: "display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 16px" },
        el("span", { style: "font-size: 44px; font-weight: 650; line-height: 1" }, EM.pct(c.share)),
        el("span", { class: "muted" }, `of draws hold two numbers in a row · fair draws: ${EM.pct(c.fairShare)}`)),
      el("div", {}, Object.entries(c.pairCounts).filter(([, count]) => count).map(([pairs, count]) =>
        el("div", { class: "list-row" }, el("span", { class: "muted" }, names[pairs] || `${pairs} pairs`),
          el("span", { class: "value" }, el("strong", {}, EM.num(count)), el("span", { class: "small muted" }, ` ${share(count)}`))))),
      el("p", { class: "small muted" }, "Longest run of numbers in a row: " +
        Object.entries(c.longestRuns).filter(([, count]) => count)
          .map(([run, count]) => `${run} (${share(count)})`).join(", ") + "."));
  }

  function range(analysis) {
    const r = analysis.ranges;
    return el("section", { class: "card", "aria-labelledby": "rangeTitle" },
      el("h2", { id: "rangeTitle", style: "font-size: 20px" }, "Range: highest minus lowest"),
      el("div", { style: "display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 16px" },
        el("span", { style: "font-size: 44px; font-weight: 650; line-height: 1" }, `${r.low} – ${r.high}`),
        el("span", { class: "muted" }, `for ${EM.pct(r.centralShare, 0)} of draws`)),
      el("div", { class: "stat-tiles", style: "grid-template-columns: repeat(2, minmax(0, 1fr))" },
        tile("Mean", r.mean.toFixed(1), `standard deviation ${r.stdev.toFixed(1)}`), tile("Median", String(r.median)),
        tile("Narrowest", String(r.min)), tile("Widest", String(r.max))),
      el("p", { class: "small muted" }, "Draws per band of 5"),
      histogram(analysis.rangeBands, 5, r, analysis.totalDraws, "the range of the five main numbers", 140));
  }

  function together(analysis) {
    const list = (title, ranking, note) => el("div", { style: "display: flex; flex-direction: column; gap: 10px" },
      el("h3", { style: "font-size: 16px" }, title),
      el("div", {}, ranking.entries.map((entry) => el("div", { class: "list-row" },
        el("span", { class: "num", style: "font-weight: 650" }, entry.combo.join(" – ")),
        el("span", { class: "value" }, EM.plural(entry.count, "time"))))),
      ranking.more ? el("p", { class: "small muted" },
        `+${ranking.more} more at ${EM.plural(ranking.entries[ranking.entries.length - 1].count, "time")}`) : null,
      el("p", { class: "small muted" }, note));
    const chance = (ranking, what) =>
      `A given ${what}: ${ranking.expected.toFixed(1)} times expected. The luckiest: about ${ranking.typicalTop} by chance alone.`;
    return el("section", { class: "card", "aria-labelledby": "togTitle" },
      el("div", { style: "display: flex; flex-direction: column; gap: 4px" },
        el("h2", { id: "togTitle" }, "Drawn together most often"),
        el("p", { class: "muted" }, "The luckiest combination always looks remarkable: under each list, what the luckiest one reaches by chance alone.")),
      el("div", { class: "cards", style: "gap: 24px" },
        list("Pairs of numbers", analysis.pairs, chance(analysis.pairs, "pair")),
        list("Triplets", analysis.triplets, chance(analysis.triplets, "triplet")),
        list("Pairs of Lucky Stars", analysis.starPairs,
          chance(analysis.starPairs, "star pair") + " Counted on the draws with 12 stars only.")));
  }
})();
