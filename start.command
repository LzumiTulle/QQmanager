#!/bin/zsh
cd "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python run.py
else
  exec python3 run.py
fi
