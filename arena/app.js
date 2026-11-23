import { renderBoard } from "./board.js";

const $ = (id) => document.getElementById(id);
const colors = ["black", "white"];
const titles = { black: "Black", white: "White" };
const rounds = ["Round of 16", "Quarterfinals", "Semifinals", "Final"];
const oracle = { id: "oracle", kind: "oracle", name: "Alpha-Beta Oracle" };
const roster = new Map();
const fields = {};
let archive = null;
let daily = null;
let dailyAvailable = true;
let rankingMode = "arena";
let worker = null;
let match = { moves: [], winner: null, players: {} };
let position = 0;
let selectedFixture = null;

function el(tag, text, css) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (css) node.className = css;
  return node;
}

async function readJSON(name) {
  const response = await fetch(new URL(name, import.meta.url), { cache: "no-store" });
  if (!response.ok) throw new Error("Data unavailable.");
  return response.json();
}

function entryName(id) {
  return roster.get(id)?.name || daily?.entrants?.[id] || "Awaiting winner";
}

function setStatus(label, text, tone = "") {
  $("board-status").lastElementChild.textContent = label;
  $("board-status").dataset.tone = tone;
  $("match-message").textContent = text;
  $("load-progress").hidden = true;
}

function showPosition(next = match.moves.length, rebuildLog = false) {
  position = Math.max(0, Math.min(next, match.moves.length));
  const all = match.moves.length;
  renderBoard($("board"), match.moves.slice(0, position), position === all && match.reason === "five" ? match.winner : null);
  $("replay-position").max = all;
  $("replay-position").value = position;
  $("replay-position").disabled = all === 0;
  $("move-count").textContent = `${position} / ${all}`;
  $("move-badge").textContent = `${all} ${all === 1 ? "move" : "moves"}`;
  $("first-move").disabled = $("prev-move").disabled = position === 0;
  $("next-move").disabled = $("last-move").disabled = position === all;
  const log = $("move-log");
  if (rebuildLog) log.replaceChildren();
  match.moves.slice(log.children.length).forEach((move) => {
    const index = log.children.length + 1;
    const item = el("li");
    const button = el("button");
    button.type = "button";
    const name = el("span", titles[move.color] + (index <= (match.opening || 0) ? " · Opening" : ""));
    name.prepend(el("i", undefined, `stone-dot ${move.color}`));
    button.append(el("span", index, "turn-index"), name, el("span", move.move, "move-coordinate"));
    button.addEventListener("click", () => showPosition(index));
    item.append(button);
    log.append(item);
  });
  log.querySelector('[aria-current="step"]')?.removeAttribute("aria-current");
  log.children[position - 1]?.firstElementChild.setAttribute("aria-current", "step");
  if (position === all) log.scrollTop = log.scrollHeight;
}

function outcome() {
  if (match.reason === "draw") return "Draw · the board filled up.";
  if (match.reason === "forfeit") return `${match.players[match.winner]} won by forfeit.`;
  if (match.winner) {
    return `${match.players[match.winner]} won${match.reason === "five" ? " · five in a row" : ""}.`;
  }
  return "Showing the latest published position.";
}

function showReplay(replay, players, label) {
  if (worker) {
    $("match-message").textContent = "Stop the live match before opening a replay.";
    $("match").scrollIntoView();
    return;
  }
  match = { ...replay, players };
  $("board-title").textContent = `${players.black} vs ${players.white}`;
  setStatus(label, outcome(), match.reason ? "complete" : "");
  showPosition(match.moves.length, true);
}

function addPlayer(color, choice) {
  const field = $("player-template").content.firstElementChild.cloneNode(true);
  const select = field.querySelector("select");
  const note = field.querySelector(".player-note");
  const custom = field.querySelector(".custom-fields");
  const token = custom.querySelector(".token");
  custom.querySelector(".cors-origin").textContent = location.origin;
  field.querySelector(".player-color").textContent = titles[color] + (color === "black" ? " · moves first" : "");
  field.querySelector(".stone-dot").classList.add(color);
  select.setAttribute("aria-label", `${titles[color]} player`);
  const option = (name, value) => new Option(name, value);
  select.append(option(oracle.name, oracle.id));
  const publicModels = el("optgroup");
  publicModels.label = "Public Hugging Face models";
  for (const model of roster.values()) publicModels.append(option(model.name, model.id));
  select.append(publicModels, option("Custom API endpoint…", "custom"));
  select.value = choice;

  function update() {
    custom.hidden = select.value !== "custom";
    custom.querySelector(".endpoint").required = !custom.hidden;
    custom.querySelector(".model-id").required = !custom.hidden;
    if (custom.hidden) token.value = "";
    note.replaceChildren();
    if (select.value === "oracle") {
      note.textContent = "GomokuBench’s standard alpha-beta search";
    } else if (select.value === "custom") {
      note.textContent = "Your browser connects directly to the API.";
    } else {
      const model = roster.get(select.value);
      const link = el("a", "Hugging Face");
      link.href = "https://huggingface.co/" + model.repo;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      note.append(link, ` · ${model.mb} MB on first use`);
    }
  }
  select.addEventListener("change", update);
  fields[color] = { field, select, custom, token };
  $("player-fields").append(field);
  update();
}

function selectedPlayer(color) {
  const { select, custom, token } = fields[color];
  if (select.value === "oracle") return oracle;
  if (select.value !== "custom") return { ...roster.get(select.value), kind: "public" };
  const endpoint = new URL(custom.querySelector(".endpoint").value);
  if (endpoint.protocol !== "https:" || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    throw new Error(`${titles[color]} needs an HTTPS endpoint URL without embedded credentials, query, or fragment.`);
  }
  endpoint.pathname = endpoint.pathname.replace(/\/+$/, "");
  if (!endpoint.pathname.endsWith("/chat/completions")) {
    endpoint.pathname = endpoint.pathname.replace(/\/$/, "") + "/chat/completions";
  }
  const model = custom.querySelector(".model-id").value.trim();
  if (!model) throw new Error(`Enter a model ID for ${titles[color]}.`);
  return { kind: "custom", name: model, endpoint: endpoint.href, model, token: token.value.trim() };
}

function setPlaying(playing) {
  $("start-match").disabled = playing;
  $("stop-match").hidden = !playing;
  colors.forEach((color) => { fields[color].field.disabled = playing; });
}

function endMatch(label, message, tone) {
  worker?.terminate();
  worker = null;
  setPlaying(false);
  setStatus(label, message, tone);
}

function startMatch(event) {
  event.preventDefault();
  if (worker) return;
  $("form-feedback").hidden = true;
  let players;
  try {
    players = Object.fromEntries(colors.map((color) => [color, selectedPlayer(color)]));
  } catch (error) {
    $("form-feedback").textContent = error instanceof TypeError ? "Enter a valid HTTPS endpoint URL." : error.message;
    $("form-feedback").hidden = false;
    return;
  }
  colors.forEach((color) => { fields[color].token.value = ""; });
  match = { moves: [], winner: null, players: Object.fromEntries(colors.map((color) => [color, players[color].name])) };
  $("board-title").textContent = `${players.black.name} vs ${players.white.name}`;
  showPosition(0, true);
  setStatus("Starting", "Preparing the players…", "live");
  setPlaying(true);
  try {
    worker = new Worker(new URL("./match-worker.js", import.meta.url), { type: "module" });
  } catch {
    endMatch("Match ended", "This browser could not start the game engine.", "error");
    return;
  }
  const current = worker;
  current.onmessage = ({ data }) => {
    if (worker !== current) return;
    if (data.type === "progress") {
      setStatus("In progress", data.message, "live");
      if (Number.isFinite(data.percent)) {
        $("load-progress").hidden = false;
        $("load-progress").value = data.percent;
      }
    } else if (data.type === "state") {
      const follow = position === match.moves.length;
      match = { ...match, ...data.state };
      showPosition(follow ? match.moves.length : position);
      if (match.reason) endMatch("Final", outcome(), "complete");
    } else if (data.type === "error") {
      endMatch("Match ended", data.message, "error");
    }
  };
  current.onerror = () => {
    if (worker === current) endMatch("Match ended", "The browser stopped the match engine. Start a new match to retry.", "error");
  };
  current.postMessage(players);
}

const dayFormat = new Intl.DateTimeFormat(undefined, { timeZone: "UTC", month: "long", day: "numeric", year: "numeric" });
const formatDay = (day) => dayFormat.format(new Date(day + "T00:00:00Z"));

function showFixtureDetail(fixture, button) {
  const detail = $("fixture-detail");
  const previous = selectedFixture;
  previous?.setAttribute("aria-expanded", "false");
  detail.replaceChildren();
  if (previous === button) {
    detail.hidden = true;
    selectedFixture = null;
    return;
  }
  selectedFixture = button;
  button.setAttribute("aria-expanded", "true");
  detail.hidden = false;
  const advancement = {
    points: "on game points", five: "with five in a row", forfeit: "by forfeit",
    "faster win": "with the faster win", "higher seed": "as the higher seed",
  };
  const pause = fixture.error === "OpenRouter/provider HTTP 429" ? "Free provider rate limited" : fixture.error;
  const state = fixture.winner ? `${entryName(fixture.winner)} advanced ${advancement[fixture.decidedBy]}.`
    : pause || "Match in progress";
  detail.append(el("p", state));
  fixture.games.forEach((game, index) => {
    const result = game.winner ? titles[game.winner] + " won" : "Draw";
    const label = game.reason ? `Replay game ${index + 1} · ${result}` : `View position · ${game.moves.length} moves`;
    const replay = el("button", label, "secondary");
    replay.type = "button";
    replay.addEventListener("click", () => {
      showReplay(game, { black: entryName(game.black), white: entryName(game.white) }, "Tournament replay");
      $("match").scrollIntoView();
    });
    detail.append(replay);
  });
  const bounds = detail.getBoundingClientRect();
  if (bounds.bottom > innerHeight || bounds.top < 0) detail.scrollIntoView({ block: "nearest" });
}

function renderTournament() {
  const bracket = $("bracket");
  bracket.replaceChildren();
  $("fixture-detail").hidden = true;
  selectedFixture = null;
  const seeds = daily?.seeds || [];
  const fixtures = daily?.rounds.flatMap((round) => round.matches) || [];
  const completed = fixtures.flatMap((match) => match?.games || []).filter((game) => game.reason).length;
  const planned = fixtures.length * (daily?.gamesPerFixture || 2);
  $("tournament-caption").textContent = daily
    ? `${daily.edition || "Latest published"} · ${formatDay(daily.day)} · ${seeds.length} models · ${completed === planned ? `${completed} games` : `${completed}/${planned} games complete`}`
    : dailyAvailable ? "The first tournament is awaiting results." : "The tournament feed could not be loaded.";
  $("tournament-method").textContent = daily?.gamesPerFixture === 1
    ? "One game per pairing. OpenRouter models play each other in the first round, and the winners meet local models in the quarterfinals. A draw advances the higher seed."
    : "Each pairing plays twice with swapped colors from the same opening. Ties go to the faster win, then to the higher seed.";
  const champion = $("champion");
  champion.hidden = !daily?.champion;
  champion.replaceChildren(el("small", "Champion"), el("strong", entryName(daily?.champion)));

  rounds.forEach((name, stage) => {
    const column = el("section", undefined, "bracket-column");
    column.style.setProperty("--matches", 8 >> stage);
    column.append(el("h3", name));
    const matches = el("div", undefined, "round-matches");
    const fixtures = daily?.rounds[stage]?.matches || Array(8 >> stage).fill(null);
    fixtures.forEach((match) => {
      const button = el("button", undefined, "fixture");
      button.type = "button";
      button.disabled = !match;
      button.setAttribute("aria-expanded", "false");
      button.setAttribute("aria-controls", "fixture-detail");
      (match?.players || [null, null]).forEach((id, slot) => {
        const row = el("span", undefined, `fixture-row${id && id === match.winner ? " winner" : ""}`);
        row.append(el("span", id ? seeds.indexOf(id) + 1 : "·", "seed"),
          el("span", id ? entryName(id) : "Awaiting result"),
          el("span", match?.score?.[slot] ?? "–", "fixture-score"));
        button.append(row);
      });
      if (match) {
        const result = match.winner ? `${entryName(match.winner)} advanced.` : match.status === "blocked" ? "Paused." : "Pending.";
        button.setAttribute("aria-label", `${name}: ${match.players.map(entryName).join(" vs ")}. ${result} View game.`);
        button.addEventListener("click", () => showFixtureDetail(match, button));
      }
      matches.append(button);
    });
    column.append(matches);
    bracket.append(column);
  });
}

function oracleRankRow(record, index, includeReplays) {
  const row = el("tr");
  const ranked = record.score !== null;
  const name = el("td");
  if (record.openrouter) {
    const link = el("a", record.model, "model-link");
    link.href = "https://openrouter.ai/" + record.openrouter;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    name.append(link);
  } else {
    name.textContent = record.model;
  }
  row.append(el("td", ranked ? index + 1 : "—"), name);
  [record.wins, record.losses, record.draws, record.score?.toFixed(2)].forEach((value) => {
    row.append(el("td", ranked ? value : "—"));
  });
  if (!includeReplays) return row;
  const games = el("td");
  const controls = el("div", undefined, "benchmark-games");
  if (!record.replays?.length) controls.append(el("span", "—"));
  (record.replays || []).forEach((game) => {
    const color = titles[game.color];
    if (game.status !== "complete") {
      controls.append(el("span", `${color} · ${game.reason || "Pending"}`));
      return;
    }
    const result = game.winner === game.color ? "Win" : (game.winner ? "Loss" : "Draw");
    const button = el("button", `${color} · ${result}`);
    button.type = "button";
    button.setAttribute("aria-label", `Replay ${record.model} as ${color}, ${result.toLowerCase()}`);
    button.addEventListener("click", () => {
      showReplay(game, game.players, "Oracle benchmark replay");
      $("match").scrollIntoView();
    });
    controls.append(button);
  });
  games.append(controls);
  row.append(games);
  return row;
}

function renderRanking() {
  $("arena-rank").setAttribute("aria-pressed", String(rankingMode === "arena"));
  $("archive-rank").setAttribute("aria-pressed", String(rankingMode === "archive"));
  const archiveRank = rankingMode === "archive";
  const openrouter = archive?.openrouter;
  const caption = $("ranking-caption");
  const placements = daily?.placementMatches || [];
  const rated = daily?.ranking || [];
  const ratedIds = new Set(rated.map((row) => row.id));
  const unrated = (archive?.ranking || [])
    .filter((row) => row.openrouter && !ratedIds.has(row.openrouter))
    .map((row) => ({ id: row.openrouter, model: row.model, elo: null }));
  const rows = archiveRank ? archive?.ranking : [...rated, ...unrated];
  if (archiveRank) {
    caption.textContent = openrouter
      ? `Results against the standard Alpha-Beta Oracle, including ${openrouter.completedGames}/${openrouter.plannedGames} completed OpenRouter free games.`
      : "Recorded GomokuBench results against the standard Alpha-Beta Oracle.";
  } else {
    caption.textContent = daily ? `Elo from published head-to-head games through ${formatDay(daily.day)}. Unrated models are awaiting a finished game.`
      : "Elo standings appear after the first published tournament.";
  }
  const header = el("tr");
  const labels = ["Rank", "Model", "Wins", "Losses", "Draws", rankingMode === "arena" ? "Elo" : "Benchmark score"];
  if (archiveRank && openrouter) labels.push("Recent game replays");
  if (!archiveRank && placements.length) labels.push("Placement game");
  labels.forEach((name) => {
    const cell = el("th", name);
    cell.scope = "col";
    header.append(cell);
  });
  $("ranking-head").replaceChildren(header);
  $("ranking-body").replaceChildren();
  if (!rows?.length) {
    const row = el("tr");
    const cell = el("td", "Awaiting results");
    cell.colSpan = labels.length;
    row.append(cell);
    $("ranking-body").append(row);
    return;
  }
  rows.forEach((record, index) => {
    if (archiveRank) {
      $("ranking-body").append(oracleRankRow(record, index, Boolean(openrouter)));
      return;
    }
    const row = el("tr");
    const values = record.elo === null
      ? ["—", record.model, "—", "—", "—", "Unrated"]
      : [index + 1, entryName(record.id), record.wins, record.losses, record.draws, Math.round(record.elo)];
    values.forEach((value) => row.append(el("td", value)));
    if (placements.length) {
      const placement = placements.find((fixture) => fixture.players[0] === record.id);
      const cell = el("td");
      const actions = el("div", undefined, "benchmark-games");
      if (placement) {
        const game = placement.games[0];
        const label = game.reason ? "Replay" : "View position";
        const replay = el("button", label);
        replay.type = "button";
        replay.setAttribute("aria-label", `${label} · ${entryName(record.id)} placement game`);
        replay.addEventListener("click", () => {
          showReplay(game, { black: entryName(game.black), white: entryName(game.white) }, "Placement replay");
          $("match").scrollIntoView();
        });
        actions.append(replay);
        if (placement.error) {
          const text = placement.error === "OpenRouter/provider HTTP 429" ? "Free provider rate limited" : placement.error;
          actions.append(el("span", text));
        }
      } else {
        actions.append(el("span", "—"));
      }
      cell.append(actions);
      row.append(cell);
    }
    $("ranking-body").append(row);
  });
}

async function refreshTournament() {
  try {
    const latest = await readJSON("./daily.json");
    if (daily && latest.generatedAt === daily.generatedAt) return;
    daily = latest.day ? latest : null;
    dailyAvailable = true;
  } catch {
    if (daily) return;
    dailyAvailable = false;
  }
  renderTournament();
  renderRanking();
}

async function init() {
  showPosition(0, true);
  $("start-match").disabled = true;
  $("match-form").addEventListener("submit", startMatch);
  $("stop-match").addEventListener("click", () => endMatch("Stopped", "Match stopped. You can replay the moves already played.", ""));
  $("first-move").addEventListener("click", () => showPosition(0));
  $("prev-move").addEventListener("click", () => showPosition(position - 1));
  $("next-move").addEventListener("click", () => showPosition(position + 1));
  $("last-move").addEventListener("click", () => showPosition(match.moves.length));
  $("replay-position").addEventListener("input", (event) => showPosition(Number(event.target.value)));
  $("arena-rank").addEventListener("click", () => { rankingMode = "arena"; renderRanking(); });
  $("archive-rank").addEventListener("click", () => { rankingMode = "archive"; renderRanking(); });
  window.addEventListener("pagehide", () => {
    Object.values(fields).forEach(({ token }) => { token.value = ""; });
    if (worker) endMatch("Stopped", "Match stopped.", "");
  });

  const [models, historical] = await Promise.allSettled([
    readJSON("./roster.json"), readJSON("./benchmark.json"),
  ]);
  if (models.status === "fulfilled") {
    models.value.forEach((model) => roster.set(model.id, model));
    addPlayer("black", "oracle");
    addPlayer("white", "smollm2-135m");
    $("start-match").disabled = false;
  } else {
    setStatus("Unavailable", "The player list could not be loaded. Refresh to try again.", "error");
  }
  if (historical.status === "fulfilled") {
    archive = historical.value;
    if (archive.replay) showReplay(archive.replay, archive.replay.players, "Benchmark replay");
  }
  await refreshTournament();
  setInterval(refreshTournament, daily?.status === "running" ? 60_000 : 5 * 60_000);
}

init();
