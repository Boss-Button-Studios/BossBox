#!/bin/bash
# Must run in project root with sudo privileges (for ollama restart)
# Usage: ./decomp_test.sh <logfile> <session_name>

if [ -z "$1" ]; then
  echo "Usage: $0 <logfile> <session_name>"
  exit 1
fi

if [ -z "$2" ]; then
  echo "Usage: $0 <logfile> <session_name>"
  exit 1
fi

LOG_FILE="$1"
SESSION_NAME="$2"

# Restart Ollama to clear VRAM state before each run
restart_ollama() {
  echo "--- [ollama restart] ---" | tee -a "$LOG_FILE"
  sudo systemctl stop ollama
  sleep 3
  sudo systemctl start ollama
  sleep 5
}

# Run a single bossbox test with label and optional extra args
run_test() {
  local label="$1"
  local goal="$2"
  shift 2
  echo "--- TEST: $label ---" | tee -a "$LOG_FILE"
  bossbox "$goal" --auto "$@" 2>&1 | tee -a "$LOG_FILE"
}

# Initialize
restart_ollama
source .venv/bin/activate

echo "--- Beginning CLI decomposition testing, session: $SESSION_NAME ---" | tee -a "$LOG_FILE"

# ---------------------------------------------------------------------------
# Basic manual suggestions
# ---------------------------------------------------------------------------
echo "---TESTING BASIC SUGGESTIONS FROM MANUAL" | tee -a "$LOG_FILE"

run_test "reverse a string" "write a Python function that reverses a string"
run_test "Flask web app" "write a Flask web app" --redirect "focus on just the route handlers, no templates"
run_test "summarize async/await" "summarize async/await" --no-color

# ---------------------------------------------------------------------------
# Custom model checks
# ---------------------------------------------------------------------------
echo "---TESTING ABILITY TO CALL OTHER MODELS" | tee -a "$LOG_FILE"

restart_ollama
run_test "explain recursion w/smollm:360m" "explain recursion" --model smollm:360m

restart_ollama
run_test "explain recursion w/qwen2.5-coder:1.5b" "explain recursion" --model qwen2.5-coder:1.5b

# ---------------------------------------------------------------------------
# Ambiguous high-level goals  (restart before each — these are long outputs)
# ---------------------------------------------------------------------------
echo "---TESTING AMBIGUOUS GOALS" | tee -a "$LOG_FILE"

restart_ollama
run_test "Launch a podcast" "Launch a podcast"

restart_ollama
run_test "Fix communication problems" "Fix my team's communication problems"

restart_ollama
run_test "Build a mobile app" "Build a mobile app"

# ---------------------------------------------------------------------------
# Tasks with hidden dependencies
# ---------------------------------------------------------------------------
echo "---TESTING TASKS WITH HIDDEN DEPENDENCIES" | tee -a "$LOG_FILE"

restart_ollama
run_test "Move to a new city" "Move to a new city"

restart_ollama
run_test "Publish a research paper" "Publish a research paper"

restart_ollama
run_test "Deploy a software product" "Deploy a software product"

# ---------------------------------------------------------------------------
# Cross-domain tasks
# ---------------------------------------------------------------------------
echo "---TESTING CROSS-DOMAIN TASKS" | tee -a "$LOG_FILE"

restart_ollama
run_test "Open restaurant" "Open a restaurant"

restart_ollama
run_test "Run a clinical trial" "Run a clinical trial"

# ---------------------------------------------------------------------------
# Ill-formed requests
# ---------------------------------------------------------------------------
echo "---TESTING ILL-FORMED REQUESTS" | tee -a "$LOG_FILE"

restart_ollama
run_test "Make it better" "Make it better"
run_test "Handle yesterday" "Handle the thing from yesterday"

# ---------------------------------------------------------------------------
# Recursive depth / granularity scaling
# ---------------------------------------------------------------------------
echo "---TESTING RECURSIVE DEPTH" | tee -a "$LOG_FILE"

restart_ollama
run_test "Write a for loop" "Write a for loop"
run_test "Build a web app" "Build a web application"

restart_ollama
run_test "Start a company" "Start a company"
