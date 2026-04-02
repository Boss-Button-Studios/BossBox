#!/bin/bash

# Check that the user provided a log file argument
if [ -z "$1" ]; then
  echo "Usage: $0 <logfile>"
  exit 1
fi

LOG_FILE="$1"

#Initialize
pkill ollama
ollama serve
source .venv/bin/activate

#All tests will be done in auto mode for the sake of expediency. Check the provided log file
#for output.

#Basic manual suggestions

bossbox "write a Python function that reverses a string" --auto 2>&1 | tee -a "$LOG_FILE"

bossbox "write a Flask web app" --auto --redirect "focus on just the route handlers, no templates" 2>&1 | tee -a "$LOG_FILE"

bossbox "summarize async/await" --auto --no-color 2>&1 | tee -a "$LOG_FILE"

#Custom model checks
bossbox "explain recursion" --auto --model smollm:360m 2>&1 | tee -a "$LOG_FILE"

bossbox "explain recursion" --auto --model qwen2.5-coder:1.5b 2>&1 | tee -a "$LOG_FILE"

#Ambiguous high level goals
bossbox "Launch a podcast" --auto 2>&1 | tee -a "$LOG_FILE"

bossbox "Fix my team's communication problems" --auto 2>&1 | tee -a "$LOG_FILE"

bossbox "Build a mobile app" --auto 2>&1 | tee -a "$LOG_FILE"

#Tasks with hidden dependencies

bossbox "Move to a new city" --auto 2>&1 | tee -a "$LOG_FILE"

bossbox "Publish a research paper" --auto 2>&1 | tee -a "$LOG_FILE"

bossbox "Deploy a software product" --auto 2>&1 | tee -a "$LOG_FILE"

#Cross-domain tasks

bossbox "Open a restaurant" --auto 2>&1 | tee -a "$LOG_FILE"

bossbox "Run a clinical trial" --auto 2>&1 | tee -a "$LOG_FILE"

#Ill-formed requests

bossbox "Make it better" --auto 2>&1 | tee -a "$LOG_FILE"

bossbox "Handle the thing from yesterday" --auto 2>&1 | tee -a "$LOG_FILE"