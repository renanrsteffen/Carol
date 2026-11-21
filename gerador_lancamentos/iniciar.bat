@echo off
REM Gerador de Lancamentos Contabeis - inicia o programa no navegador
cd /d "%~dp0"
if not exist .venv (
  echo Instalando o programa pela primeira vez, aguarde...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
streamlit run app.py --server.headless false --browser.gatherUsageStats false
pause
