"""
BossBox CLI — BossBox Atomic Step 16
======================================
Command-line entry point.  Accepts a goal string, runs it through the full
Supervisor pipeline, and streams stage transitions and model reasoning to the
terminal.

Usage
-----
  bossbox "your goal here"
  bossbox "your goal" --auto
  bossbox "your goal" --redirect "focus on X instead"
  bossbox "your goal" --model smollm:1.7b

Flags
-----
  --auto            Trust Mode — skip the human decomposition checkpoint.
  --redirect TEXT   Apply a redirect at the checkpoint instead of prompting.
  --model MODEL     Ollama model to use (default: smollm:1.7b).
  --no-color        Disable ANSI colour output.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from bossbox.audit.logger import AuditLogger
from bossbox.pipeline.decomposer import DecompositionResult
from bossbox.pipeline.envelope import TaskEnvelope, create_envelope
from bossbox.pipeline.supervisor import Supervisor
from bossbox.providers.ollama import OllamaProvider

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "smollm:1.7b"
_DEFAULT_AUDIT_PATH = Path.home() / ".bossbox" / "audit" / "audit.log"

# ---------------------------------------------------------------------------
# Terminal output helpers
# ---------------------------------------------------------------------------

_BOLD  = "\033[1m"
_DIM   = "\033[2m"
_CYAN  = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED   = "\033[31m"
_RESET = "\033[0m"

_USE_COLOR = True  # toggled by --no-color or non-tty


def _c(text: str, *codes: str) -> str:
    if not _USE_COLOR:
        return text
    return "".join(codes) + text + _RESET


def _print_stage(stage: str) -> None:
    label = stage.ljust(10)
    print(_c(f"[{label}]", _CYAN, _BOLD), flush=True)


def _print_thought(source: str, content: str) -> None:
    if not content:
        return
    if source == "progress":
        print(f"  {_c('→', _DIM)} {content}", flush=True)
    else:
        # reasoning — indent and dim
        for line in content.splitlines():
            if line.strip():
                print(f"  {_c(line, _DIM)}", flush=True)


def _print_separator(char: str = "─", width: int = 60) -> None:
    print(_c(char * width, _DIM))


def _print_plan(result: DecompositionResult) -> None:
    _print_separator()
    print(_c("  Decomposition Plan", _BOLD))
    _print_separator()
    print(_c("  Core tasks:", _BOLD))
    for i, task in enumerate(result.core_tasks, 1):
        print(f"    {i}. {_c(task.title, _BOLD)}")
        if task.description:
            print(f"       {task.description}")
    if result.suggested_tasks:
        print(_c("\n  Suggested (optional):", _DIM))
        for task in result.suggested_tasks:
            print(f"    • {task.title}")
    if result.reasoning and not result.reasoning.startswith("[Decomposition"):
        print(_c("\n  Reasoning:", _DIM))
        for line in result.reasoning.splitlines():
            if line.strip():
                print(f"    {_c(line, _DIM)}")
    _print_separator()


def _print_result(result: str | None) -> None:
    _print_separator("═")
    print(_c("  Result", _BOLD, _GREEN))
    _print_separator("═")
    print(result or "(no result)")
    print()


def _print_error(msg: str) -> None:
    print(_c(f"  ✗ {msg}", _RED), file=sys.stderr)


# ---------------------------------------------------------------------------
# Async input (non-blocking prompt)
# ---------------------------------------------------------------------------

async def _async_input(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


# ---------------------------------------------------------------------------
# Checkpoint interaction
# ---------------------------------------------------------------------------

async def _handle_checkpoint(
    supervisor: Supervisor,
    redirect: str | None,
) -> None:
    """
    Handle the human checkpoint.

    If *redirect* is provided it is applied immediately.
    Otherwise the user is prompted interactively.
    """
    decomp: DecompositionResult | None = supervisor._decomposition
    if decomp is not None:
        _print_plan(decomp)

    if redirect is not None:
        print(_c(f"  → Applying redirect: {redirect}", _YELLOW))
        await supervisor.redirect(redirect)
        return

    while True:
        try:
            response = await _async_input(
                _c("  Proceed? [y]es / [n]o / [r]edirect > ", _BOLD)
            )
        except (EOFError, KeyboardInterrupt):
            print()
            supervisor.abort()
            return

        response = response.strip()

        if response.lower() in ("y", "yes", ""):
            await supervisor.approve_checkpoint()
            return
        elif response.lower() in ("n", "no"):
            supervisor.abort()
            return
        elif response.lower().startswith("r ") or response.lower().startswith("redirect "):
            _, _, direction = response.partition(" ")
            direction = direction.strip()
            if direction:
                await supervisor.redirect(direction)
                return
            print("  Please supply a direction after 'r '.")
        else:
            print("  Enter y, n, or r <direction>.")


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------

async def _run(goal: str, auto: bool, redirect: str | None, model: str) -> int:
    """
    Run the full pipeline for *goal* and return an exit code (0=success).
    """
    global _USE_COLOR
    if not sys.stdout.isatty():
        _USE_COLOR = False

    _print_separator("═")
    print(_c("  BossBox", _BOLD, _CYAN))
    print(f"  Goal: {goal}")
    print(_c(f"  Model: {model}", _DIM))
    if auto:
        print(_c("  Trust Mode (--auto)", _DIM))
    _print_separator("═")
    print()

    envelope: TaskEnvelope = create_envelope(goal, auto_approve=auto)
    provider = OllamaProvider(model=model)
    audit_logger = AuditLogger(log_path=_DEFAULT_AUDIT_PATH)

    # Wire thought stream → terminal in real time.
    _orig_add_thought = envelope.add_thought

    def _streaming_add_thought(source: str, content: str) -> None:
        _orig_add_thought(source, content)
        _print_thought(source, content)

    envelope.add_thought = _streaming_add_thought  # type: ignore[method-assign]

    # Wire stage transitions → terminal.
    supervisor = Supervisor(
        envelope=envelope,
        provider=provider,
        audit_logger=audit_logger,
        model=model,
    )

    _orig_log_transition = supervisor._log_transition

    def _printing_log_transition(stage: str) -> None:
        _orig_log_transition(stage)
        _print_stage(stage)

    supervisor._log_transition = _printing_log_transition  # type: ignore[method-assign]

    # Drive the pipeline, handling the checkpoint interactively.
    run_task = asyncio.create_task(supervisor.run())

    while not run_task.done():
        await asyncio.sleep(0.05)
        if envelope.status == "paused" and not supervisor._aborted:
            await _handle_checkpoint(supervisor, redirect)
            redirect = None  # consume redirect — only apply once

    await run_task

    print()
    if envelope.status == "complete":
        _print_result(envelope.result)
        return 0
    else:
        _print_error(f"Pipeline did not complete (status: {envelope.status}).")
        return 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bossbox",
        description="BossBox — local-first AI workbench.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "goal",
        help="Goal for BossBox to accomplish.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Trust Mode: skip the human decomposition checkpoint.",
    )
    parser.add_argument(
        "--redirect",
        metavar="TEXT",
        default=None,
        help="Apply a redirect at the checkpoint instead of prompting.",
    )
    parser.add_argument(
        "--model",
        default=_DEFAULT_MODEL,
        help=f"Ollama model to use (default: {_DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour output.",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    global _USE_COLOR
    if args.no_color:
        _USE_COLOR = False

    exit_code = asyncio.run(_run(
        goal=args.goal,
        auto=args.auto,
        redirect=args.redirect,
        model=args.model,
    ))
    sys.exit(exit_code)
