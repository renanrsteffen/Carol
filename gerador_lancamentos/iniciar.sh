#!/usr/bin/env bash
# Gerador de Lançamentos Contábeis - inicia o programa no navegador
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
else
  . .venv/bin/activate
fi
streamlit run app.py --browser.gatherUsageStats false
