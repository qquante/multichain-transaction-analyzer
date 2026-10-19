# Multichain Transaction Analyzer

> **Local private review:** publication is paused. See [security review and remaining release conditions](docs/SECURITY_REVIEW.md). Local fixes have not been pushed.


A resumable EVM/Solana transaction research pipeline with request budgets, cached evidence and ranked output.

![review](https://img.shields.io/badge/review-private-374151) ![primary](https://img.shields.io/badge/primary-Python-2563EB) [![LinkedIn](https://img.shields.io/badge/LinkedIn-Douglas%20Silva-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/douglas-silva-data-analyst/)

[Overview](#overview) · [Architecture](#architecture) · [Setup](#setup) · [Validation](#validation) · [Roadmap](#roadmap) · [Review notes](docs/REVIEW.md)

## Overview

- Separate EVM and Solana readers normalize transaction evidence.
- Request budgets and checkpoints bound collection and support resumption.
- Ranking and verification modules expose evidence-qualified views.

**Technology:** Python · SQLite · HTTP/RPC readers · JSON/CSV · pycryptodome

**Persistence and data:** SQLite stores checkpoints/cache; JSON/CSV reports are generated locally. Prior wallet research output is excluded.

## Architecture

```text
Authorized research seeds → budgeted chain readers → SQLite cache/checkpoints → verification and ranked reports
```

| Location | Review focus |
| --- | --- |
| [`wallet_forensics/pipeline.py`](wallet_forensics/pipeline.py) | Source/configuration inspected for scope and structure |
| [`wallet_forensics/evm.py`](wallet_forensics/evm.py) | Source/configuration inspected for scope and structure |
| [`wallet_forensics/solana.py`](wallet_forensics/solana.py) | Source/configuration inspected for scope and structure |
| [`wallet_forensics/storage.py`](wallet_forensics/storage.py) | Source/configuration inspected for scope and structure |
| [`wallet_forensics/ranking.py`](wallet_forensics/ranking.py) | Source/configuration inspected for scope and structure |

Internal package and module names are retained where changing them would affect imports or compatibility. This descriptive repository name does not imply a completed package/API migration.

## Setup

Python >=3.11. The preparation copy restores a normal wallet_forensics package layout and adds pyproject.toml. Install with python -m pip install -e . and run python -m unittest discover -s tests -p test_forensics.py -v. Live collection requires an explicit local provider and budget.

Start from an isolated environment. Use `.env.example` when present and inspect the configuration loader: an example file is not necessarily loaded automatically. Runtime databases, real account sessions and private credentials are not bundled. Live integration prerequisites are separate from local tests.

## Validation

Static parsing checked 16 Python files and 0 strict JSON files. Python syntax errors: 0; JSON errors: 0. The selected offline suite passed.

```console
python -m unittest discover -s tests -p test_forensics.py -v
```

These results apply to the selected checks on the preparation copy. They do not establish deployment readiness, full coverage, live provider availability or measured performance. CI definitions, when present under `docs/ci-templates/`, are inactive review templates; no remote CI badge is claimed.

## Limits and current status

RPC/API collection can consume provider quota. Public transaction correlation does not by itself prove who owns a wallet. No live investigation or external query was run.

This private snapshot is for code and portfolio review. Generated runtime material and unreviewed binary assets may be excluded. [Review notes](docs/REVIEW.md) record preparation scope, findings and outstanding validation.

## Roadmap

- [ ] Publish a synthetic seed and deterministic local fixture walkthrough.
- [ ] Run tests/test_forensics.py in the expected package layout.
- [ ] Review provenance and uncertainty labels in generated reports.

## History and attribution

No recoverable original Git history was verified for this snapshot. Import and preparation commits use their actual current dates; filesystem timestamps are not treated as development dates.

No open-source license is granted by this preparation. A license and attribution review is required before public redistribution.

## Maintainer and feedback

Douglas de Oliveira Silva · [GitHub](https://github.com/qquante) · [LinkedIn](https://www.linkedin.com/in/douglas-silva-data-analyst/)

For this private review, report reproducible issues with a minimal fixture and the exact check output. Keep account data, credentials and generated sensitive reports out of issues and commits. Existing contribution guidelines apply where present.

**Topics:** `python`, `sqlite`, `ethereum`, `solana`, `data-analysis`, `blockchain-analytics`
