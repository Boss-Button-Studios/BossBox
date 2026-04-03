# BossBox

**Local-first AI workbench — Boss Button Studios**

BossBox manages a tiered fleet of small language models, orchestrates them into
agentic pipelines, and provides a supervised human-in-the-loop interface for
accomplishing complex tasks. It runs entirely on your hardware — no cloud
account, no telemetry, no paid tier.

> **Development status:** Steps 1–17 of 24 are complete. The core pipeline
> is functional end-to-end via CLI. The GUI milestone begins at Step 22. See
> [docs/bossbox_spec_v4.3.1.md](docs/bossbox_spec_v4.3.1.md) for the full roadmap.

---

## What's built so far

| Step | Component | What it does |
|------|-----------|-------------|
| 1 | Project Scaffold | Package structure, entry points, config files |
| 2 | Configuration Loader | YAML config with environment variable expansion |
| 3 | Task Envelope | Central pipeline data structure |
| 4 | Audit Logger | Append-only JSONL audit trail at `~/.bossbox/audit/` |
| 5–6 | Provider Layer | Ollama, Anthropic, OpenAI via uniform interface; fallback chains |
| 7 | Secrets Manager | AES-256-GCM encrypted secrets; password, keychain, or hardware token unlock |
| 8 | VRAM Budgeter | Background thread; evicts lower-priority models before loading; Nano always hot |
| 9 | Document Sanitizer | Strips hidden PDF layers, DOCX hidden runs, `display:none` HTML; OCR escalation via tesseract |
| 10 | Injection Detection | Skill profile for detecting prompt injection in incoming documents |
| 11 | Document Type Coherence | Profiles that validate document type consistency |
| 12 | Linguistic Analysis Agent | Heuristic and model-based text analysis |
| 13 | Backup Manager | Workspace snapshot and restore with sandbox enforcement |
| 14 | Task Decomposer | Breaks complex tasks into ordered subtasks via model call |
| 15 | Supervisor State Machine | Async orchestrator; routes subtasks through the model tier chain |
| 16 | CLI Runner | End-to-end `bossbox run` command; human-in-the-loop confirmation prompts |
| 17 | Notification Service | Desktop and terminal notifications for pipeline events |

---

## Prerequisites

- Python 3.12+
- [Ollama](https://ollama.com) running locally with at least one model pulled
- `tesseract-ocr` for deep-mode document sanitization (`sudo apt install tesseract-ocr`)

Recommended models (matched to the development hardware tier):

```bash
ollama pull smollm:360m        # nano — always loaded
ollama pull smollm:1.7b        # micro
ollama pull qwen2.5-coder:1.5b # specialist
ollama pull deepseek-r1:7b     # reasoner
```

---

## Setup

```bash
git clone https://github.com/Boss-Button-Studios/BossBox
cd BossBox
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Development

```bash
# Activate the venv first
source .venv/bin/activate

# Run the full regression suite
pytest --tb=short -v

# Run with coverage
pytest --cov=bossbox --cov-report=term-missing

# Run a single step's tests
pytest tests/vram/ -v
```

The test suite has 1231+ passing tests across all completed steps.
Every step ships two test files: `test_<name>.py` (pytest) and
`test_<name>_unittest.py` (stdlib unittest, no network required).

---

## Architecture

BossBox is built as a staffing agency for AI assistants. The supervisor
orchestrates a tiered fleet of models:

| Tier | Role | Default model |
|------|------|---------------|
| Nano | Always-on responder | smollm:360m |
| Micro | Decomposition, analysis | smollm:1.7b |
| Specialist | Domain-specific tasks | qwen2.5-coder:1.5b |
| Reasoner | Complex reasoning | deepseek-r1:7b |

Every external document passes through the physical sanitizer before any
model sees it. All model calls go through the Provider Abstraction Layer.
Every action is logged to an append-only audit trail. The human is always
in the loop.

See [docs/bossbox_spec_v4.3.1.md](docs/bossbox_spec_v4.3.1.md) for the full
specification including the complete step sequence, security model, and
hardware guidance.

---

## License

Apache License 2.0 — see `LICENSE`.

The hypervisor-isolated self-audit mechanism is intentionally contributed
to the public domain of ideas. No patent protection sought.
