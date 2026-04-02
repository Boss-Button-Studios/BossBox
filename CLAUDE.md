# BossBox — Claude Code Session Briefing

**Studio:** Boss Button Studios  
**Repo:** https://github.com/Boss-Button-Studios/BossBox  
**Local root:** `~/Projects/BBS/BossBox`  
**Spec:** `docs/bossbox_spec_v4.3.1.md` — read this before any implementation work.

---

## What This Project Is

BossBox is a local-first AI workbench — a "staffing agency for AI assistants." It manages
a tiered fleet of small language models, orchestrates them into agentic pipelines, and
provides a supervised human-in-the-loop interface. It is free, open source, and built to
run on modest hardware without cloud dependencies.

Core values that affect every design decision: local-first, honest about constraints,
security without obscurity, user in control, free and open, resource-conscious.

---

## Current State

| Step | Content | Status |
|------|---------|--------|
| 1 | Project Scaffold | ✅ Done |
| 2 | Configuration Loader | ✅ Done |
| 3 | Task Envelope Dataclass | ✅ Done |
| 4 | Audit Logger | ✅ Done |
| 5 | Provider Base + Ollama | ✅ Done |
| 6 | Provider Registry | ✅ Done |
| 7 | Secrets Manager | ✅ Done |
| 8 | VRAM Budgeter | ✅ Done |
| 9 | Physical Document Sanitizer | ✅ Done |
| 10| Injection Detection Skill Profile | ✅ Done |
| 11| Document Type Coherence Profiles | ✅ Done |
| 12| Linguistic Analysis Agent | ✅ Done |
| 13| Backup Manager | ✅ Done |
| 14| Task Decomposer | ✅ Done |
| 15| Supervisor State Machine | ✅ Done |
| 16| CLI Runner | ✅ Done |
| 17| Notification Service | ✅ Done |
| 18| Skill Profile Loader and Validator
| 19| Skill Elicitor
| 20| RAG Corpus Indexer
| 21| Hypervisor Process
| 22| GUI Shell v1
| 23| Onboarding Wizard
| 24| PyInstaller Build Script


The cumulative test suite has 1057+ passing tests. Every new step must leave the full
suite still passing.

Post step 16v2, playtest results are being kept in BossBox/playtest_logs.

---

## The Atomic Step Protocol — READ THIS FIRST

This project is built one atomic step at a time. Each step has a defined spec entry in
`docs/bossbox_spec_v4.3.1.md` Section 17 with inputs, outputs, and acceptance criteria.

**The trigger phrase for implementation is:**
> "Let's do Step N" or "Execute Atomic Step N"

**Do not write implementation code until that trigger is given.**

Before starting any step:
1. Read the step's spec entry in full.
2. Check the repo to confirm expected files from the prior step are present.
3. Confirm the prior step's tests pass before touching anything.
4. Present a file manifest (what will be created or modified) for confirmation if the
   step is complex or touches existing files.

Each step closes with:
- All new tests passing
- Full regression suite passing
- Test output saved to `test_results/regression_stepNN.txt`
- User docs (CLI and  GUI) updated and/or created for each step
- Files committed and pushed

---

## Environment Setup

```bash
# Always activate the venv first
source .venv/bin/activate

# Verify environment
python -m pytest --version
python -c "import bossbox; print('package ok')"
```

Python version: 3.12  
Package manager: pip via pyproject.toml  
Install: `pip install -e ".[dev]"` (editable install, dev extras)

---

## Test Conventions

### Two-file test pattern (mandatory for every step)

Every step produces **two test files** — identical coverage, different runners:

| File | Runner | Purpose |
|------|--------|---------|
| `tests/[module]/test_[name].py` | pytest | Primary test file |
| `tests/[module]/test_[name]_unittest.py` | stdlib unittest | Fallback for no-network environments |

The unittest file must be runnable with:
```bash
python -m unittest tests.[module].test_[name]_unittest -v
```
And also collected automatically by pytest.

### Regression test command (run after every step)

```bash
source .venv/bin/activate
pytest --tb=short -v 2>&1 | tee test_results/regression_stepNN.txt
```

Replace `NN` with the zero-padded step number (e.g., `08`, `09`).

### Step-specific test command

```bash
pytest tests/[module]/test_[name].py -v 2>&1 | tee test_results/stepNN_[name].txt
```

### Async tests

The project uses `pytest-asyncio` in `AUTO` mode (configured in `pyproject.toml`).
Async test functions need no decorator — just `async def test_...`.

### Key test dependencies already installed
- `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`
- `respx` — for mocking `httpx` HTTP calls (used in provider tests)
- `anyio`

---

## Code Conventions

### File length limit
**600 lines maximum per file.** If a module is growing past this, split it. Discuss
the split before implementing it — don't silently reorganize.

### Docstring header pattern
Every implementation file opens with a docstring naming the step:
```python
"""
[Module Name] — BossBox Atomic Step N
=======================================
[Brief description of what this module does and why.]
"""
```

### Import style
- Standard library first, then third-party, then local `bossbox.*`
- `from __future__ import annotations` at the top of every file

### Error handling
- Raise specific exception subclasses, not bare `Exception`
- Every module that can fail has its own exception hierarchy
- Example: `bossbox/secrets/exceptions.py` defines `SecretsException` subtypes

### Async
- Provider calls are async (`httpx.AsyncClient`)
- The supervisor will be fully async — do not introduce blocking calls in the pipeline
- Use `asyncio.gather` for concurrent operations where appropriate

### Security-sensitive code
- Secrets, keys, and credentials are **never** written to logs, audit trails, or
  test output — not even in test fixtures
- File permissions on sensitive files: 600 on Unix (use `os.chmod(path, 0o600)`)
- The audit logger is append-only and never truncates

---

## Architecture Highlights

These affect implementation decisions at every step:

**Separation of concerns:**
- Supervisor = thin orchestrator, no ML dependencies
- Models invoked via HTTP only (through Provider Abstraction Layer)
- Hypervisor = separate process, structurally isolated from pipeline (Step 21)
- GUI thread never blocks on model calls — queues only (Step 22)

**Key data structure:** `TaskEnvelope` (`bossbox/pipeline/envelope.py`) is the central
pipeline object. Everything flows through it. Read its fields before any pipeline work.

**Provider abstraction:** All model calls go through `ProviderRegistry`
(`bossbox/providers/registry.py`). Never call Ollama/Anthropic/OpenAI directly.
Missing provider keys register as `None` silently — no exception.

**Audit trail:** Every significant action is logged to
`~/.bossbox/audit/audit.log` (append-only JSONL) via `bossbox/audit/logger.py`.

**Work area sandbox:** The system writes only to `~/.bossbox/workspace/`.
Paths outside this raise `OutsideWorkAreaError` (enforced in Step 13 onward).

**VRAM Budgeter** (Step 8): Background thread. Must be consulted before any tier
invocation — `request_load(model)` returns True only when budget allows.
Nano model is NEVER evicted. Eviction priority (lowest first): Reasoner → Specialist
→ Micro → Nano.

---

## Hardware Context

Development machine: Dell Latitude 5480  
- NVIDIA discrete GPU, ~2GB VRAM  
- 16GB RAM  
- Running Ollama with: smollm 360m/1.7b, qwen2.5-coder 1.5b, deepseek-r1 7b

This hardware sits at the "minimum viable" tier in the spec. The VRAM Budgeter
behavior will reflect real memory pressure — tests should account for constrained
budgets, not assume unlimited VRAM.

---

## Git Workflow

```bash
# Standard step closeout sequence
source .venv/bin/activate
pytest --tb=short -v 2>&1 | tee test_results/regression_stepNN.txt
git add -A
git commit -m "Step N: [Step Name] — NNN tests passing"
git push
```

Global git config has `pull.rebase true` set.

**Do not use bash heredocs for file content** — they have caused encoding issues.
Write files directly via the editor or file creation tools.

---

## Repository Layout (key paths)

```
BossBox/
├── CLAUDE.md                    ← you are here
├── PRINCIPLES.md                ← core values, read before any product decision
├── docs/
│   └── bossbox_spec_v4.3.1.md    ← authoritative spec, Section 17 = step sequence
├── bossbox/                     ← main package
│   ├── audit/logger.py          ← append-only JSONL audit trail
│   ├── config/loader.py         ← YAML config with env var expansion
│   ├── pipeline/envelope.py     ← TaskEnvelope (central data structure)
│   ├── providers/               ← OllamaProvider, AnthropicProvider, registry
│   ├── secrets/manager.py       ← AES-256-GCM secrets, three-factor unlock
│   └── vram/budgeter.py         ← PLACEHOLDER — Step 8 target
├── config/
│   ├── providers.yaml
│   └── tiers.yaml
├── skills/default/              ← default skill profiles (YAML, no code)
├── tests/                       ← mirrors bossbox/ package structure
│   ├── config/
│   ├── pipeline/
│   ├── providers/
│   └── secrets/
└── test_results/                ← regression output files, committed to repo
```

---

## What's a Placeholder vs. What's Implemented

Several files exist but contain only stubs with `raise NotImplementedError(...)`.
These are scaffolding from Step 1, not partial implementations. Before working on
any file, check whether it's a real implementation or a placeholder — size is a
reliable signal (placeholders are typically under 10 lines).

Current placeholders (as of Step 7 closeout):
- `bossbox/vram/budgeter.py` — Step 8
- `bossbox/ingest/sanitizer.py` — Step 9
- `bossbox/ingest/analyzer.py` — Step 12
- `bossbox/pipeline/backup.py` — Step 13
- `bossbox/pipeline/decomposer.py` — Step 14
- `bossbox/pipeline/supervisor.py` — Step 15
- `bossbox/skills/loader.py` — Step 18
- `bossbox/skills/elicitor.py` — Step 19
- `bossbox/hypervisor/hypervisor.py` — Step 21
- `bossbox/notify/notifier.py` — Step 17
- `bossbox/gui/app.py`, `wizard.py`, `security_center.py` — Step 22/23

---

## The IBM Principle

This appears in the GUI (Step 22) but should inform every decision made earlier:

> *"A computer cannot be held accountable, therefore a computer must never make a decision."*

BossBox surfaces this permanently in the Task Input and Pipeline View tabs.
It is not a warning — it is a design statement. The human is always in the loop.

---

## Questions Before Starting Any Step

1. Is the prior step's regression output in `test_results/`?
2. Does `git log --oneline -5` show the expected last commit?
3. Does the full suite pass clean from a fresh `pytest` run?

If any answer is no, resolve it before writing new code.
