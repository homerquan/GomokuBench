---
title: Gomoku Arena
emoji: ⚫
colorFrom: green
colorTo: gray
sdk: static
app_file: index.html
fullWidth: true
license: apache-2.0
short_description: AI models play Gomoku with live games and a Top 16 bracket.
custom_headers:
  cross-origin-embedder-policy: require-corp
  cross-origin-opener-policy: same-origin
  cross-origin-resource-policy: cross-origin
---

# Gomoku Arena

Play freestyle Gomoku on the 19 × 19 [GomokuBench](https://github.com/homerquan/GomokuBench) board. Select two public Hugging Face models, the standard Alpha-Beta Oracle, or a custom OpenAI-compatible endpoint. Move history supports step-by-step replay. Tournament pairings open both recorded games.

Public GGUFs run on your device through WebAssembly. Each model shows its approximate first-use download size. Use the direct Space app URL to give the browser access to multiple inference threads. The public models choose a legal move from open intersections adjacent to existing stones, with nine center openings available on an empty board.

Custom endpoints need HTTPS and CORS access for the Space app origin. The browser sends the token directly to that endpoint as a bearer credential and clears the form when a match starts. Use a temporary token and revoke it in the provider’s settings when finished.

## Tournament and rankings

The daily local bracket uses two games per pairing with swapped colors and a shared two-stone opening. Game points determine advancement. Ties go to the faster win, then the higher seed.

The OpenRouter Challenge starts with the eight rated OpenRouter challengers and eight top local entrants in separate first-round pairings. The winners meet in the quarterfinals. Each pairing plays one game, and a draw advances the higher seed. Repeated illegal replies can cause a forfeit. Network and provider interruptions pause the fixture.

Arena Elo starts at 1500 and changes after each completed head-to-head board game with K=24. The Oracle benchmark uses GomokuBench’s separate score. Exhibitions are local to the browser.

OpenRouter models awaiting a completed head-to-head game appear as unrated. Placement games can establish a rating outside the knockout.

## OpenRouter games against the Oracle

Oracle benchmark includes the archived GomokuBench results and the zero-priced OpenRouter chat runs. Each free model gets one game as Black and one as White against the standard Alpha-Beta Oracle. The benchmark rewards wins, fewer moves to win, and more moves survived in losses. Recent OpenRouter games can be replayed from that table.

Run the benchmark from the checkout and enter the OpenRouter API key at the hidden prompt. The runner enforces zero-price routing, checks reported response costs, and checkpoints the games locally so interrupted runs can continue. The Space bundle includes the validated results and move coordinates.

```sh
python -m arena.openrouter_bench --prompt-key
python -m arena.publish --publish
```

To run the mixed head-to-head bracket from the same checkout, start the knockout worker with the local bracket and OpenRouter results available.

```sh
python -m arena.openrouter_knockout --placement-games
python -m arena.publish --publish --daily .cache/arena/openrouter-knockout.json
```

## Development and publishing

From the benchmark checkout, build a staging folder and serve it with a local HTTP server.

```sh
python -m arena.publish --build /tmp/gomoku-space
python -m http.server 7860 --directory /tmp/gomoku-space
```

Publish the bundle using an account write token available as `HF_TOKEN`. Publishing uploads the listed Space files, the game-engine modules they import, and sanitized benchmark results.

```sh
python -m pip install -r arena/requirements.txt
python -m arena.publish --publish
```

Run the tournament worker once per UTC day from a scheduler. It updates the Space with a complete bracket, all 30 replays, and the Elo standings. The worker reads yesterday’s published Elo and skips dates already published. The most recent successful tournament remains visible between updates.

```sh
python -m arena.daily --publish
```
