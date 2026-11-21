#!/usr/bin/env bash
# Gerador de Lançamentos Contábeis - inicia o programa no navegador
cd "$(dirname "$0")"
if [ ! -x .venv/bin/streamlit ]; then
  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
.venv/bin/python -m streamlit run app.py --browser.gatherUsageStats false --server.address localhost
