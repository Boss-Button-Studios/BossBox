"""Step 1 — Project Scaffold acceptance tests."""
import importlib, pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).parent.parent

MODULES = [
    "bossbox", "bossbox.config.loader", "bossbox.providers.base",
    "bossbox.providers.ollama", "bossbox.pipeline.envelope",
    "bossbox.pipeline.supervisor", "bossbox.pipeline.decomposer",
    "bossbox.pipeline.backup", "bossbox.hypervisor.hypervisor",
    "bossbox.ingest.sanitizer", "bossbox.ingest.analyzer",
    "bossbox.skills.loader", "bossbox.skills.elicitor",
    "bossbox.audit.logger", "bossbox.notify.notifier",
    "bossbox.vram.budgeter", "bossbox.gui.app",
    "bossbox.gui.wizard", "bossbox.gui.security_center", "bossbox.cli",
]

@pytest.mark.parametrize("module_name", MODULES)
def test_module_importable(module_name):
    importlib.import_module(module_name)

def test_version_present():
    import bossbox
    assert hasattr(bossbox, "__version__") and bossbox.__version__

EXPECTED_FILES = [
    "pyproject.toml", "README.md", "PRINCIPLES.md",
    "bossbox/__init__.py", "bossbox/config/loader.py",
    "bossbox/providers/base.py", "bossbox/providers/ollama.py",
    "bossbox/pipeline/envelope.py", "bossbox/pipeline/supervisor.py",
    "bossbox/pipeline/decomposer.py", "bossbox/pipeline/backup.py",
    "bossbox/hypervisor/hypervisor.py", "bossbox/ingest/sanitizer.py",
    "bossbox/ingest/analyzer.py", "bossbox/skills/loader.py",
    "bossbox/skills/elicitor.py", "bossbox/audit/logger.py",
    "bossbox/notify/notifier.py", "bossbox/vram/budgeter.py",
    "bossbox/gui/app.py", "bossbox/gui/wizard.py",
    "bossbox/gui/security_center.py", "bossbox/cli.py",
    "config/providers.yaml", "config/tiers.yaml", "skills/default/README.md",
]

@pytest.mark.parametrize("filepath", EXPECTED_FILES)
def test_file_exists(filepath):
    assert (ROOT / filepath).exists(), f"Missing: {filepath}"

def test_entry_point():
    assert "bossbox.cli:main" in (ROOT / "pyproject.toml").read_text()

def test_principles_content():
    text = (ROOT / "PRINCIPLES.md").read_text()
    assert "democratize" in text.lower()
    assert "Boss Button Studios" in text

def test_providers_yaml():
    import yaml
    cfg = yaml.safe_load((ROOT / "config/providers.yaml").read_text())
    assert all(k in cfg["providers"] for k in ("ollama", "anthropic", "openai"))

def test_tiers_yaml():
    import yaml
    t = yaml.safe_load((ROOT / "config/tiers.yaml").read_text())
    assert all(k in t["tiers"] for k in ("nano","micro","specialist","reasoner","cloud"))
    assert t["tiers"]["nano"]["always_loaded"] is True
    assert "nano" not in t["eviction_priority"]

def test_cli_callable():
    from bossbox import cli
    assert callable(cli.main)


def test_sanitize_not_implemented():
    from bossbox.ingest.sanitizer import sanitize
    with pytest.raises(NotImplementedError): sanitize(b"x", "f.pdf")


def test_vram_budgeter_implemented():
    from bossbox.vram.budgeter import VRAMBudgeter
    b = VRAMBudgeter(vram_budget_bytes=2 * 1024**3, auto_start=False)
    assert isinstance(b.request_load("smollm:360m"), bool)
