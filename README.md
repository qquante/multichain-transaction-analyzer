# Multichain Transaction Analyzer

A resumable EVM/Solana transaction research pipeline with request budgets, cached evidence and ranked output.

## Features

- Separate EVM and Solana readers normalize transaction evidence.
- Request budgets and checkpoints bound collection and support resumption.
- Ranking and verification modules expose evidence-qualified views.

## Stack

Python · SQLite · HTTP/RPC readers · JSON/CSV · pycryptodome

SQLite stores checkpoints/cache; JSON/CSV reports are generated locally. Prior wallet research output is excluded.

## Getting started

Python >=3.11. Install with python -m pip install -e . and run python -m unittest discover -s tests -p test_forensics.py -v. Live collection requires an explicit local provider and budget.

## How it works

```text
Authorized research seeds → budgeted chain readers → SQLite cache/checkpoints → verification and ranked reports
```

## Current limitations

RPC/API collection can consume provider quota. Public transaction correlation does not by itself prove who owns a wallet. No live investigation or external query was run.

## Tests

```console
python -m unittest discover -s tests -p test_forensics.py -v
```

These checks use offline fixtures or controlled backends.

## License

No open-source license has been granted. Redistribution terms remain to be defined.
