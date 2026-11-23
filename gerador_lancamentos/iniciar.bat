@echo off
chcp 65001 >nul
setlocal
REM ============================================================================
REM  Gerador de Lancamentos Contabeis - inicia o programa no navegador
REM  Funciona com a pasta no proprio computador ou na rede (\\servidor\...).
REM  O programa e os dados (pasta "dados") ficam onde este arquivo esta;
REM  as bibliotecas do Python ficam instaladas em cada computador.
REM ============================================================================

REM pushd aceita caminho de rede: cria uma letra de unidade temporaria
pushd "%~dp0"
if errorlevel 1 (
  echo Nao foi possivel acessar a pasta do programa: %~dp0
  pause
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Python nao encontrado. Instale o Python 3.12 em https://www.python.org/downloads/
  echo  e marque a opcao "Add python.exe to PATH" durante a instalacao.
  echo.
  popd
  pause
  exit /b 1
)

set "AMBIENTE=%LOCALAPPDATA%\GeradorLancamentos\venv"
set "PY=%AMBIENTE%\Scripts\python.exe"

REM instala na primeira vez ou quando a lista de bibliotecas mudar
set "INSTALAR="
if not exist "%PY%" set "INSTALAR=1"
if exist "%PY%" (
  fc /b "requirements.txt" "%AMBIENTE%\requirements.txt" >nul 2>nul || set "INSTALAR=1"
)

if defined INSTALAR (
  echo Instalando as bibliotecas neste computador. Aguarde alguns minutos...
  if not exist "%PY%" python -m venv "%AMBIENTE%"
  if not exist "%PY%" (
    echo Erro ao criar o ambiente do Python em %AMBIENTE%
    popd
    pause
    exit /b 1
  )
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Erro na instalacao. Verifique a internet e tente de novo.
    popd
    pause
    exit /b 1
  )
  copy /y "requirements.txt" "%AMBIENTE%\requirements.txt" >nul
)

echo.
echo Abrindo o programa no navegador... (feche esta janela para encerrar)
"%PY%" -m streamlit run app.py --browser.gatherUsageStats false --server.address localhost
popd
pause
