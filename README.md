# Gomoku CLI

A small Python command-line Gomoku game built from the original alpha-beta Gomoku engine.

## Requirements

- Python 3
- `numpy`

## Install

```bash
pip install -r requirements.txt
```

## Play

```bash
python3 gomoku.py play
```

Optional flags:

- `--player black|white`
- `--ai-first`

The CLI always uses a `19x19` board and AI search depth `2`.

Moves use `x,y` with 1-based coordinates, for example `10,10`.

## Benchmark

Run an LLM against the built-in alpha-beta AI:

```bash
python3 gomoku.py benchmark --model nemotron-3-super -r 10
```

To watch the rounds play out in the console while benchmarking:

```bash
python3 gomoku.py benchmark --model nemotron-3-super -r 10 -v
```

What this does:

- Loads the model config from `models/nemotron-3-super.json`
- Runs 10 rounds total
- Uses balanced starts: 5 rounds with the AI moving first and 5 rounds with the LLM moving first
- Always uses a `19x19` board
- Always uses AI search depth `2`
- `-v` prints each round, move, and board state in the console
- Saves the benchmark report to `benchmarks/nemotron-3-super.json`

The benchmark report is saved as JSON and includes the summary plus per-game move logs and final boards.

## Adding Models

This repo now includes a few example model configs in the `models/` folder.

You can add another model by creating a new JSON config that uses an OpenAI-compatible chat completions API format.

In general:

- add a new config file under `models/`
- point it at an OpenAI-compatible `baseURL`
- set the remote `model` name
- add any required API key env var to `.env`

Examples in this repo include Ollama-compatible, Hugging Face Router, and OpenRouter model configs.
