@echo off
chcp 65001 >nul
REM Gerador de Lancamentos Contabeis - inicia o programa no navegador
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Python nao encontrado. Instale o Python 3.12 em https://www.python.org/downloads/
  echo  e marque a opcao "Add Python to PATH" durante a instalacao.
  echo.
  pause
  exit /b 1
)

if not exist .venv\Scripts\streamlit.exe (
  echo Instalando o programa pela primeira vez. Aguarde alguns minutos...
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Erro na instalacao. Verifique a internet e tente de novo.
    pause
    exit /b 1
  )
)

echo Abrindo o programa no navegador... (feche esta janela para encerrar)
.venv\Scripts\python -m streamlit run app.py --browser.gatherUsageStats false --server.address localhost
pause
