# BossBox

**Local-first AI workbench — Boss Button Studios**

BossBox manages a tiered fleet of small language models, orchestrates them into agentic pipelines, and provides a supervised human-in-the-loop interface for accomplishing complex tasks. It ships as a self-contained installable package that detects your hardware, recommends appropriate models, and is immediately useful without configuration.

Free, open source, no telemetry, no paid tier.

---

## Quick Start

```bash
pip install -e ".[dev]"
bossbox "Summarize the key points in this document" --file report.pdf
```

## Development

```bash
# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run full regression test suite
pytest tests/ -v --tb=short

# Run with coverage
pytest tests/ --cov=bossbox --cov-report=term-missing
```

## Architecture

See `bossbox_spec_v3.md` for the full specification.

## License

Apache License 2.0 — see `LICENSE`.

The hypervisor-isolated self-audit mechanism is intentionally contributed to the public domain of ideas. No patent protection sought.
