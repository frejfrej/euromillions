// The Patterns screen: how the five main numbers of a draw sit together.
"use strict";

(() => {
  const { el, tr } = EM;

  EM.start("patterns.html", ["Patterns", "Répartitions"], async (status, container) => {
    const analysis = await EM.get("/api/analysis");
    const total = EM.num(analysis.totalDraws);
    container.replaceChildren(
      el("div", { class: "intro" },
        el("h1", {}, tr("Patterns of the five main numbers", "Répartition des cinq numéros")),
        el("p", {}, tr(`How the numbers of a draw sit together, over all ${total} draws. ` +
          "Where fair draws give an exact figure, it is shown next to what happened.",
        `Comment se répartissent les numéros d’un tirage, sur l’ensemble des ${total} tirages. ` +
          "Quand des tirages équitables donnent une valeur exacte, elle figure à côté de ce qui s’est produit."))),
      EM.disclaimer(),
      sums(analysis),
      el("div", { class: "cards", style: "grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr)); gap: 24px" },
        split(analysis, tr("Odd and even", "Pairs et impairs"),
          tr("How many of the five numbers are odd", "Combien des cinq numéros sont impairs"),
          analysis.oddCounts, [tr("odd", "impair"), tr("even", "pair")], [tr("odd", "impairs"), tr("even", "pairs")]),
        split(analysis, tr("Low and high", "Bas et hauts"),
          tr(`Low is 1 to ${analysis.lowMax}, high is ${analysis.lowMax + 1} to 50`,
            `Bas de 1 à ${analysis.lowMax}, hauts de ${analysis.lowMax + 1} à 50`),
          analysis.lowCounts, [tr("low", "bas"), tr("high", "haut")], [tr("low", "bas"), tr("high", "hauts")])),
      el("div", { class: "cards", style: "grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr)); gap: 24px" },
        consecutive(analysis), range(analysis)),
      together(analysis));
  });

  const tile = (label, value, note) => el("div", { class: "tile" },
    el("span", { class: "small muted" }, label), el("span", { class: "value" }, value),
    note ? el("span", { class: "small muted" }, note) : null);

  const ofDraws = (share) => tr(`${EM.pct(share, 0)} of draws`, `${EM.pct(share, 0)} des tirages`);

  // A histogram of ``bands`` of ``step`` values, with the central share of ``dist`` marked.
  function histogram(bands, step, dist, total, what, height) {
    const peak = Math.max(...bands.map((band) => band.draws));
    const room = (100 * (height - 28)) / height; // the tallest bar leaves room for the label
    const index = (value) => bands.findIndex((band) => value < band.from + step);
    const first = index(dist.low) + 1;
    const last = index(dist.high) + 1;
    const largest = bands.reduce((best, band) => (band.draws > best.draws ? band : best));
    const every = Math.ceil(bands.length / 10);
    const draws = (n) => EM.count(n, "draw", "tirage");
    return el("div", {
      class: "histogram", role: "img",
      style: `grid-template-columns: repeat(${bands.length}, minmax(0, 1fr)); grid-template-rows: ${height}px auto`,
      "aria-label": tr(
        `Histogram of ${what}: ${EM.pct(dist.centralShare, 0)} of draws between ${dist.low} and ${dist.high}; ` +
        `the most common band is ${largest.from} to ${largest.from + step - 1}, with ${draws(largest.draws)}.`,
        `Histogramme ${what}${EM.NNBSP}: ${EM.pct(dist.centralShare, 0)} des tirages entre ${dist.low} et ${dist.high}${EM.NNBSP}; ` +
        `la tranche la plus fréquente va de ${largest.from} à ${largest.from + step - 1}, avec ${draws(largest.draws)}.`),
    },
      el("div", { class: "band", style: `grid-column: ${first} / ${last + 1}` }, ofDraws(dist.centralShare)),
      bands.map((band, i) => el("div", {
        class: "bar", title: `${band.from}–${band.from + step - 1}${tr(":", `${EM.NNBSP}:`)} ${draws(band.draws)} (${EM.pct(band.draws / total)})`,
        style: `grid-column: ${i + 1}; height: ${(room * band.draws / peak).toFixed(1)}%`,
      })),
      el("div", { class: "baseline" }),
      bands.map((band, i) => el("span", { class: "tick", style: `grid-column: ${i + 1}` }, i % every ? "" : band.from)));
  }

  const median = (value) => EM.fixed(value, Number.isInteger(value) ? 0 : 1);

  function sums(analysis) {
    const sums = analysis.sums;
    return el("section", { class: "card", "aria-labelledby": "sumTitle" },
      el("div", { class: "card-head" },
        el("div", {}, el("h2", { id: "sumTitle" }, tr("Sum of the five numbers", "Somme des cinq numéros")),
          el("p", { class: "muted" }, tr("Draws per band of 10", "Tirages par tranche de 10")))),
      el("div", { class: "stat-tiles" },
        tile(tr("Mean", "Moyenne"), EM.fixed(sums.mean), tr(`fair draws: ${EM.fixed(sums.fairMean)}`,
          `tirages équitables${EM.NNBSP}: ${EM.fixed(sums.fairMean)}`)),
        tile(tr("Median", "Médiane"), median(sums.median)),
        tile(ofDraws(sums.centralShare), `${sums.low} – ${sums.high}`),
        tile(tr("Lowest · highest", "Minimum · maximum"), `${sums.min} · ${sums.max}`,
          tr(`standard deviation ${EM.fixed(sums.stdev)}`, `écart-type ${EM.fixed(sums.stdev)}`))),
      histogram(analysis.sumBands, 10, sums, analysis.totalDraws,
        tr("the sum of the five main numbers", "de la somme des cinq numéros"), 220));
  }

  function split(analysis, title, sub, counts, [yes, no], [yesMany, noMany]) {
    const total = analysis.totalDraws;
    const rows = [5, 4, 3, 2, 1, 0].map((k) => ({
      label: `${k} ${k > 1 ? yesMany : yes} · ${5 - k} ${5 - k > 1 ? noMany : no}`,
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
        title: tr(`${row.label}: ${EM.count(row.count, "draw")} (${EM.pct(row.share)}), fair draws ${EM.pct(row.fair)}`,
          `${row.label}${EM.NNBSP}: ${EM.count(row.count, "draw", "tirage")} (${EM.pct(row.share)}), tirages équitables ${EM.pct(row.fair)}`),
      },
        el("span", { class: "small" }, row.label),
        el("div", { class: "track", "aria-hidden": "true" },
          el("div", { class: "fill", style: `width: ${(100 * row.share / scale).toFixed(1)}%` }),
          el("div", { class: "mark", style: `left: calc(${(100 * row.fair / scale).toFixed(1)}% - 1px)` })),
        el("span", { class: "small num", style: "font-weight: 650; text-align: right" }, EM.pct(row.share))))),
      el("div", { class: "legend" },
        el("span", {}, el("span", { class: "swatch", style: "background: var(--bar); height: 10px" }), tr("Share of draws", "Part des tirages")),
        el("span", {}, el("span", { class: "swatch", style: "background: var(--ink); width: 2px" }), tr("Fair draws", "Tirages équitables"))));
  }

  function consecutive(analysis) {
    const c = analysis.consecutive;
    const names = [tr("No neighbours", "Aucun voisin"), tr("One pair", "Une paire"), tr("Two pairs", "Deux paires"),
      tr("Three pairs", "Trois paires"), tr("Four pairs", "Quatre paires")];
    const share = (n) => (n && n / c.draws < 0.001 ? tr("under 0.1%", `moins de ${EM.pct(0.001)}`) : EM.pct(n / c.draws));
    return el("section", { class: "card", "aria-labelledby": "consTitle" },
      el("h2", { id: "consTitle", style: "font-size: 20px" }, tr("Consecutive numbers", "Numéros consécutifs")),
      el("div", { style: "display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 16px" },
        el("span", { style: "font-size: 44px; font-weight: 650; line-height: 1" }, EM.pct(c.share)),
        el("span", { class: "muted" }, tr(`of draws hold two numbers in a row · fair draws: ${EM.pct(c.fairShare)}`,
          `des tirages ont deux numéros qui se suivent · tirages équitables${EM.NNBSP}: ${EM.pct(c.fairShare)}`))),
      el("div", {}, Object.entries(c.pairCounts).filter(([, n]) => n).map(([pairs, n]) =>
        el("div", { class: "list-row" }, el("span", { class: "muted" }, names[pairs] || pairs),
          el("span", { class: "value" }, el("strong", {}, EM.num(n)), el("span", { class: "small muted" }, ` ${share(n)}`))))),
      el("p", { class: "small muted" }, tr("Longest run of numbers in a row: ", `Plus longue suite de numéros${EM.NNBSP}: `) +
        Object.entries(c.longestRuns).filter(([, n]) => n).map(([run, n]) => `${run} (${share(n)})`).join(", ") + "."));
  }

  function range(analysis) {
    const r = analysis.ranges;
    return el("section", { class: "card", "aria-labelledby": "rangeTitle" },
      el("h2", { id: "rangeTitle", style: "font-size: 20px" },
        tr("Range: highest minus lowest", `Étendue${EM.NNBSP}: plus haut moins plus bas`)),
      el("div", { style: "display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 16px" },
        el("span", { style: "font-size: 44px; font-weight: 650; line-height: 1" }, `${r.low} – ${r.high}`),
        el("span", { class: "muted" }, tr(`for ${EM.pct(r.centralShare, 0)} of draws`, `pour ${EM.pct(r.centralShare, 0)} des tirages`))),
      el("div", { class: "stat-tiles", style: "grid-template-columns: repeat(2, minmax(0, 1fr))" },
        tile(tr("Mean", "Moyenne"), EM.fixed(r.mean), tr(`standard deviation ${EM.fixed(r.stdev)}`, `écart-type ${EM.fixed(r.stdev)}`)),
        tile(tr("Median", "Médiane"), median(r.median)),
        tile(tr("Narrowest", "La plus faible"), String(r.min)), tile(tr("Widest", "La plus large"), String(r.max))),
      el("p", { class: "small muted" }, tr("Draws per band of 5", "Tirages par tranche de 5")),
      histogram(analysis.rangeBands, 5, r, analysis.totalDraws,
        tr("the range of the five main numbers", "de l’étendue des cinq numéros"), 140));
  }

  function together(analysis) {
    const times = (n) => EM.count(n, "time", ["fois", "fois"]);
    const list = (title, ranking, note) => el("div", { style: "display: flex; flex-direction: column; gap: 10px" },
      el("h3", { style: "font-size: 16px" }, title),
      el("div", {}, ranking.entries.map((entry) => el("div", { class: "list-row" },
        el("span", { class: "num", style: "font-weight: 650" }, entry.combo.join(" – ")),
        el("span", { class: "value" }, times(entry.count))))),
      ranking.more ? el("p", { class: "small muted" }, tr(
        `+${ranking.more} more at ${times(ranking.entries[ranking.entries.length - 1].count)}`,
        `+${ranking.more} autres à ${times(ranking.entries[ranking.entries.length - 1].count)}`)) : null,
      el("p", { class: "small muted" }, note));
    const chance = (ranking, en, fr) => tr(
      `A given ${en}: ${EM.fixed(ranking.expected)} times expected. The luckiest: about ${ranking.typicalTop} by chance alone.`,
      `${fr}${EM.NNBSP}: ${EM.fixed(ranking.expected)} fois attendu. La plus chanceuse${EM.NNBSP}: environ ${ranking.typicalTop} par le seul hasard.`);
    return el("section", { class: "card", "aria-labelledby": "togTitle" },
      el("div", { style: "display: flex; flex-direction: column; gap: 4px" },
        el("h2", { id: "togTitle" }, tr("Drawn together most often", "Sortis ensemble le plus souvent")),
        el("p", { class: "muted" }, tr(
          "The luckiest combination always looks remarkable: under each list, what the luckiest one reaches by chance alone.",
          `La combinaison la plus chanceuse paraît toujours remarquable${EM.NNBSP}: sous chaque liste, ce qu’atteint la plus chanceuse par le seul hasard.`))),
      el("div", { class: "cards", style: "gap: 24px" },
        list(tr("Pairs of numbers", "Paires de numéros"), analysis.pairs, chance(analysis.pairs, "pair", "Une paire donnée")),
        list(tr("Triplets", "Triplets"), analysis.triplets, chance(analysis.triplets, "triplet", "Un triplet donné")),
        list(tr("Pairs of Lucky Stars", "Paires d’étoiles"), analysis.starPairs,
          chance(analysis.starPairs, "star pair", "Une paire d’étoiles donnée") +
          tr(" Counted on the draws with 12 stars only.", " Comptées sur les tirages à 12 étoiles seulement."))));
  }
})();
