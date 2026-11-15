// The Numbers screen: next and latest draw, every number and star against chance, rankings.
"use strict";

(() => {
  const { el, tr } = EM;
  const SHOWN = 6; // entries per ranking card
  const state = { window: 50, recent: false, kind: "main", n: null };
  let analysis = null;
  let status = null;
  let container = null;
  let clock = null;

  EM.start("index.html", ["Numbers", "Numéros"], async (current, target) => {
    status = current;
    container = target;
    await refresh();
  });

  async function refresh(focusKey) {
    analysis = await EM.get(`/api/analysis?window=${state.window}&top=${SHOWN}`);
    if (state.n === null) state.n = analysis.mostDrawn.length ? analysis.mostDrawn[0].number : 1;
    render(focusKey);
  }

  function render(focusKey) {
    container.replaceChildren(draws(), EM.disclaimer(), numbersAndDetail(), rankings());
    if (focusKey) {
      const button = container.querySelector(`[data-key="${focusKey}"]`);
      if (button) button.focus();
    }
  }

  const lastDraws = (n) => tr(`Last ${n} draws`, `${n} derniers tirages`);

  // --- Next and latest draw ---

  function draws() {
    const next = new Date(status.nextDraw);
    const parts = EM.parisParts(next);
    const timeText = () => tr(`${parts.hour}:${parts.minute} Paris time · ${EM.countdown(next)}`,
      `${parts.hour}h${parts.minute}, heure de Paris · ${EM.countdown(next)}`);
    const when = el("span", {}, timeText());
    clearInterval(clock);
    clock = setInterval(() => { when.textContent = timeText(); }, 60000);

    const latest = status.latest;
    const jackpot = latest.jackpotWon === null ? ""
      : latest.jackpotWon ? tr("Jackpot won", "Jackpot remporté") : tr("Jackpot not won", "Jackpot non remporté");
    return el("section", { class: "row", "aria-label": tr("Draws", "Tirages") },
      el("div", { class: "card", style: "gap: 6px" },
        el("p", { class: "eyebrow" }, tr("Next draw", "Prochain tirage")),
        el("p", { style: "font-size: 28px; font-weight: 650; line-height: 1.2" },
          EM.capitalize(`${parts.weekday} ${parts.day} ${parts.month}`)),
        el("p", { class: "muted" }, when)),
      el("div", { class: "card", style: "gap: 12px; flex-grow: 2" },
        el("p", { class: "eyebrow" }, `${tr("Latest draw", "Dernier tirage")} · ${EM.longDate(latest.date)}`),
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
    const what = kind === "star" ? tr("Star", "Étoile") : tr("Number", "Numéro");
    const label = tr(`${what} ${stats.number}: drawn ${EM.count(stats.count, "time")}, z-score ${EM.signed(stats.z, 2)}`,
      `${what} ${stats.number}${EM.NNBSP}: ${EM.count(stats.count, ["sortie", "sorties"], ["sortie", "sorties"])}, score z ${EM.signed(stats.z, 2)}`);
    return el("button", {
      type: "button", class: "cell", "data-key": key, title: label, "aria-label": label,
      "aria-pressed": String(state.kind === kind && state.n === stats.number),
      style: `background: ${bin.bg}; color: ${bin.fg}`,
      onclick: () => select(kind, stats.number, key),
    }, el("span", { class: "n" }, stats.number), el("span", { class: "z", "aria-hidden": "true" }, EM.signed(stats.z, 1)));
  }

  function periodControls() {
    const toggle = (recent, text) => el("button", {
      type: "button", "aria-pressed": String(state.recent === recent), "data-key": recent ? "period-recent" : "period-full",
      onclick: (event) => { state.recent = recent; render(event.currentTarget.dataset.key); },
    }, text);
    return el("div", { class: "row", style: "align-items: flex-end; gap: 12px 24px" },
      el("div", { class: "field", style: "flex: 0 1 auto; min-width: 0" },
        el("span", { class: "label", id: "periodLabel" }, tr("Period", "Période")),
        el("div", { class: "segmented", role: "group", "aria-labelledby": "periodLabel" },
          toggle(false, tr("Full history", "Tout l’historique")), toggle(true, lastDraws(analysis.window)))),
      el("div", { class: "field", style: "flex: 0 1 auto; min-width: 0" },
        el("span", { class: "label", id: "windowLabel" }, tr("Recent window", "Période récente")),
        EM.windowChoice(state.window, async (w) => {
          state.window = w;
          await refresh(`window-${w}`);
        }, "windowLabel")));
  }

  function legend() {
    const item = (color, text, ring) => el("span", {},
      el("span", { class: "swatch", style: `background: ${color}${ring ? "; box-shadow: inset 0 0 0 1px #d6d4cc" : ""}` }), text);
    return el("div", { class: "legend" },
      el("span", { style: "font-weight: 650; color: var(--ink)" }, tr("z-score", "Score z")),
      item("var(--z-low2)", tr("−2 or less", "−2 ou moins")), item("var(--z-low1)", tr("−2 to −1", "de −2 à −1")),
      item("var(--z-mid)", tr("−1 to +1", "de −1 à +1"), true), item("var(--z-high1)", tr("+1 to +2", "de +1 à +2")),
      item("var(--z-high2)", tr("+2 or more", "+2 ou plus")));
  }

  function poolNote() {
    const byDate = {};
    for (const [star, day] of Object.entries(analysis.starPoolSince)) (byDate[day] = byDate[day] || []).push(star);
    const parts = Object.entries(byDate).map(([day, stars]) => {
      const and = tr("and", "et");
      const names = stars.length === 1
        ? tr(`star ${stars[0]}`, `l’étoile ${stars[0]}`)
        : tr(`stars ${stars.slice(0, -1).join(", ")} ${and} ${stars[stars.length - 1]}`,
          `les étoiles ${stars.slice(0, -1).join(", ")} ${and} ${stars[stars.length - 1]}`);
      return tr(`${names} in ${EM.longMonth(day)}`, `${names} en ${EM.longMonth(day)}`);
    });
    return tr(`${EM.capitalize(parts.join(", "))} joined the pool: each star is compared with the draws it could come up in.`,
      `${EM.capitalize(parts.join(", "))} ont rejoint le tirage${EM.NNBSP}: chaque étoile est comparée aux tirages où elle pouvait sortir.`);
  }

  function numbersAndDetail() {
    const total = EM.num(analysis.totalDraws);
    const period = state.recent
      ? tr(`in the last ${analysis.window} draws`, `sur les ${analysis.window} derniers tirages`)
      : tr(`over all ${total} draws`, `sur l’ensemble des ${total} tirages`);
    const chance = analysis.chance;
    const mainCard = el("section", { class: "card", "aria-labelledby": "mainTitle" },
      el("div", { class: "card-head" },
        el("div", {},
          el("h2", { id: "mainTitle" }, tr("Main numbers", "Numéros")),
          el("p", { class: "muted" }, tr(`Times drawn against what fair draws would give, ${period}.`,
            `Nombre de sorties face à ce que donneraient des tirages équitables, ${period}.`)))),
      periodControls(),
      el("div", { class: "scroll-x" },
        el("div", { class: "number-grid main" }, statsOf("main").map((s) => cell("main", s)))),
      legend(),
      el("p", { class: "small muted" }, tr(
        "Blue: drawn more often than expected; red: less often. The z-score is the distance to the " +
        "expected count, in standard deviations. Fair draws typically put the most extreme of 50 " +
        `z-scores near ±${EM.fixed(chance.extremeZ.main)}, so even the darkest cell is what chance alone produces.`,
        `Bleu${EM.NNBSP}: sorti plus souvent que prévu${EM.NNBSP}; rouge${EM.NNBSP}: moins souvent. Le score z est l’écart au ` +
        "nombre de sorties attendu, en écarts-types. Des tirages équitables placent d’ordinaire le plus extrême des 50 " +
        `scores z vers ±${EM.fixed(chance.extremeZ.main)}${EM.NNBSP}: même la case la plus foncée est ce que produit le hasard seul.`)));
    const starCard = el("section", { class: "card", "aria-labelledby": "starTitle" },
      el("div", { class: "card-head" },
        el("div", {},
          el("h2", { id: "starTitle" }, tr("Lucky Stars", "Étoiles")),
          el("p", { class: "muted" }, poolNote()))),
      el("div", { class: "scroll-x" },
        el("div", { class: "number-grid stars" }, statsOf("star").map((s) => cell("star", s)))),
      el("p", { class: "small muted" }, tr(
        `Same colours as above. For 12 stars, fair draws put the most extreme z-score near ±${EM.fixed(chance.extremeZ.star)}.`,
        `Mêmes couleurs que ci-dessus. Pour 12 étoiles, des tirages équitables placent le score z le plus extrême vers ±${EM.fixed(chance.extremeZ.star)}.`)));
    return el("div", { class: "split" },
      el("div", { class: "wide" }, mainCard, starCard),
      el("div", { class: "side" }, detail()));
  }

  function detail() {
    const star = state.kind === "star";
    const s = statsOf(state.kind).find((entry) => entry.number === state.n);
    const kind = star ? "star" : "main";
    const draws = (n) => EM.count(n, "draw", "tirage");
    const rows = [
      [tr("Times drawn", "Sorties"), EM.num(s.count),
        (state.recent ? tr(`in ${analysis.window} draws · `, `en ${analysis.window} tirages · `) : "") +
        tr(`expected ${EM.fixed(s.expected)}`, `attendu ${EM.fixed(s.expected)}`)],
      [tr("z-score", "Score z"), EM.signed(s.z, 2), ""],
      [tr("Share of draws", "Part des tirages"), EM.pct(s.frequency, 1), ""],
      [tr("Average gap", "Écart moyen"), s.averageGap === null ? "—" : tr(`${EM.fixed(s.averageGap)} draws`, `${EM.fixed(s.averageGap)} tirages`), ""],
      [tr("Current gap", "Écart actuel"), s.currentGap === 0 ? tr("drawn last time", star ? "sortie au dernier tirage" : "sorti au dernier tirage") : draws(s.currentGap), ""],
      [tr("Longest gap", "Plus long écart"), draws(s.longestGap), ""],
      [tr("Last seen", "Dernière sortie"), s.lastSeen ? EM.date(s.lastSeen) : tr("not in this period", "pas sur cette période"), ""],
    ];
    const since = star ? analysis.starPoolSince[s.number] : null;
    if (since) rows.push([tr("In the pool since", "Dans le tirage depuis le"), EM.date(since), ""]);
    const total = state.recent ? Math.min(analysis.window, analysis.totalDraws) : analysis.totalDraws;
    if (s.eligible < total) rows.push([tr("Draws it could come up in", star ? "Tirages où elle pouvait sortir" : "Tirages où il pouvait sortir"), EM.num(s.eligible), ""]);
    const drawn = star ? "Sortie" : "Sorti";
    const summary = s.z >= 1 ? tr("Drawn more often than expected", `${drawn} plus souvent que prévu`)
      : s.z <= -1 ? tr("Drawn less often than expected", `${drawn} moins souvent que prévu`)
        : tr("Close to what fair draws give", "Proche de ce que donnent des tirages équitables");
    const pool = star ? 12 : 50;
    const z = EM.fixed(analysis.chance.extremeZ[kind]);
    const gap = analysis.chance.longestGap[kind];
    return el("aside", { class: "card", "aria-labelledby": "detailTitle" },
      el("p", { class: "eyebrow" }, `${star ? tr("Lucky Star", "Étoile") : tr("Main number", "Numéro")} · ` +
        (state.recent ? lastDraws(analysis.window).toLowerCase() : tr("full history", "tout l’historique"))),
      el("div", { style: "display: flex; align-items: center; gap: 16px" },
        EM.ball(s.number, { star, size: "large" }),
        el("div", { style: "display: flex; flex-direction: column; gap: 2px; min-width: 0" },
          el("h2", { id: "detailTitle" }, `${star ? tr("Star", "Étoile") : tr("Number", "Numéro")} ${s.number}`),
          el("p", { class: "muted" }, summary))),
      el("div", {}, rows.map(([label, value, note]) => el("div", { class: "list-row" },
        el("span", { class: "muted" }, label),
        el("span", { class: "value" }, el("strong", {}, value), note ? el("span", { class: "small muted" }, ` ${note}`) : null)))),
      el("p", { class: "note" }, tr(
        `Fair draws typically put the most extreme of ${pool} z-scores near ±${z}, and the longest current absence near ${gap} draws.` +
        (state.recent ? " Gaps are counted within the period shown." : ""),
        `Des tirages équitables placent d’ordinaire le plus extrême des ${pool} scores z vers ±${z}, et la plus longue absence en cours vers ${gap} tirages.` +
        (state.recent ? " Les écarts sont comptés dans la période affichée." : ""))));
  }

  // --- Rankings ---

  function rankingCard(title, sub, entries, starEntries, describe, note) {
    return el("section", { class: "card", style: "gap: 12px" },
      el("div", { style: "display: flex; flex-direction: column; gap: 2px" },
        el("h3", {}, title), el("p", { class: "small muted" }, sub)),
      el("div", {}, entries.map((s) => {
        const [main, side] = describe(s, false);
        return el("div", { class: "ranking-entry" }, EM.ball(s.number, { size: "small" }),
          el("span", { class: "what" }, main), el("span", { class: "small muted num" }, side));
      })),
      starEntries.length
        ? el("div", { class: "chips", "aria-label": tr("Lucky Stars", "Étoiles") },
          el("span", { class: "small muted", style: "align-self: center" }, tr("Stars", "Étoiles")),
          starEntries.slice(0, 3).map((s) => el("span", { class: "chip" }, `${s.number} · ${describe(s, true).join(" · ")}`)))
        : null,
      el("p", { class: "small muted", style: "border-top: 1px solid var(--line-soft); padding-top: 10px" }, note));
  }

  function rankings() {
    const counted = (s) => [EM.count(s.count, "time", ["fois", "fois"]), `z ${EM.signed(s.z, 1)}`];
    const absent = (s, star) => [EM.count(s.currentGap, "draw", "tirage"),
      s.lastSeen ? tr(`since ${EM.date(s.lastSeen)}`, `depuis le ${EM.date(s.lastSeen)}`) : tr("never drawn", star ? "jamais sortie" : "jamais sorti")];
    const z = EM.fixed(analysis.chance.extremeZ.main);
    const w = analysis.window;
    const top = tr(`Fair draws put the top of 50 z-scores near +${z}.`, `Des tirages équitables placent le plus haut des 50 scores z vers +${z}.`);
    const bottom = tr(`Fair draws put the bottom of 50 z-scores near −${z}.`, `Des tirages équitables placent le plus bas des 50 scores z vers −${z}.`);
    const gaps = analysis.chance.longestGap;
    return el("section", { class: "cards", "aria-label": tr("Rankings", "Classements") },
      rankingCard(tr("Most drawn", "Les plus sortis"),
        tr("Over the whole history, against what each number could do", "Sur tout l’historique, face à ce que chaque numéro pouvait faire"),
        analysis.mostDrawn, analysis.mostDrawnStars, counted, top),
      rankingCard(tr("Least drawn", "Les moins sortis"), tr("Over the whole history", "Sur tout l’historique"),
        analysis.leastDrawn, analysis.leastDrawnStars, counted, bottom),
      rankingCard(tr(`Hot · last ${w} draws`, `Chauds · ${w} derniers tirages`),
        tr("Drawn more often than expected recently", "Sortis plus souvent que prévu récemment"),
        analysis.hot, analysis.hotStars, counted, top),
      rankingCard(tr(`Cold · last ${w} draws`, `Froids · ${w} derniers tirages`),
        tr("Drawn less often than expected recently", "Sortis moins souvent que prévu récemment"),
        analysis.cold, analysis.coldStars, counted, bottom),
      rankingCard(tr("Most overdue", "Les plus en retard"),
        tr("Longest wait since the last appearance", "Plus longue attente depuis la dernière sortie"),
        analysis.overdue, analysis.overdueStars, absent,
        tr(`Fair draws put the longest current absence near ${gaps.main} draws (${gaps.star} for stars).`,
          `Des tirages équitables placent la plus longue absence en cours vers ${gaps.main} tirages (${gaps.star} pour les étoiles).`)));
  }
})();
