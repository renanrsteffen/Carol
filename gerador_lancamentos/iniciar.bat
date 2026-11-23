@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
REM ============================================================================
REM  Gerador de Lancamentos Contabeis - inicia o programa no navegador
REM  Funciona com a pasta no proprio computador ou na rede (\\servidor\...).
REM  O programa e os dados (pasta "dados") ficam onde este arquivo esta;
REM  as bibliotecas do Python ficam instaladas em cada computador.
REM ============================================================================

pushd "%~dp0"
if errorlevel 1 (
  echo Nao foi possivel acessar a pasta do programa: %~dp0
  pause
  exit /b 1
)

set "AMBIENTE=%LOCALAPPDATA%\GeradorLancamentos\venv"
set "PY=%AMBIENTE%\Scripts\python.exe"
set "CHECA=import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,14) and sys.maxsize>2**32 else 1)"

REM ambiente ja existente mas com Python incompativel (ex.: tentativa anterior): refaz
if exist "%PY%" (
  "%PY%" -c "%CHECA%" >nul 2>nul
  if errorlevel 1 (
    echo O ambiente instalado usa uma versao do Python incompativel. Refazendo...
    rmdir /s /q "%AMBIENTE%"
  )
)

set "INSTALAR="
if not exist "%PY%" set "INSTALAR=1"
if exist "%PY%" (
  fc /b "requirements.txt" "%AMBIENTE%\requirements.txt" >nul 2>nul || set "INSTALAR=1"
)

if defined INSTALAR (
  REM escolhe um Python compativel: 3.13, 3.12, 3.11, 3.10 (64 bits) pelo "py", senao o "python"
  set "PYBASE="
  for %%V in (3.13 3.12 3.11 3.14 3.10) do (
    if not defined PYBASE (
      py -%%V-64 -c "%CHECA%" >nul 2>nul && set "PYBASE=py -%%V-64"
    )
  )
  if not defined PYBASE (
    python -c "%CHECA%" >nul 2>nul && set "PYBASE=python"
  )
  if not defined PYBASE (
    echo.
    echo  ============================================================================
    echo   Nenhum Python compativel encontrado neste computador.
    for /f "delims=" %%P in ('python --version 2^>^&1') do echo   Encontrado: %%P
    echo.
    echo   Instale o Python 3.13 ^(64 bits^) em https://www.python.org/downloads/
    echo   marcando "Add python.exe to PATH". Pode manter o Python atual instalado.
    echo   Depois abra o programa de novo.
    echo  ============================================================================
    echo.
    popd
    pause
    exit /b 1
  )
  echo Instalando as bibliotecas neste computador com !PYBASE!. Aguarde alguns minutos...
  if not exist "%PY%" !PYBASE! -m venv "%AMBIENTE%"
  if not exist "%PY%" (
    echo Erro ao criar o ambiente do Python em %AMBIENTE%
    popd
    pause
    exit /b 1
  )
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install --prefer-binary --only-binary=pandas,pyarrow,numpy,rapidfuzz -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Erro na instalacao. Verifique a internet e tente de novo.
    echo Se o erro continuar, mande uma foto desta janela.
    rmdir /s /q "%AMBIENTE%" >nul 2>nul
    popd
    pause
    exit /b 1
  )
  copy /y "requirements.txt" "%AMBIENTE%\requirements.txt" >nul
)

REM "iniciar.bat oculto" = chamado pelo "Abrir Gerador.vbs", sem janela e sem abrir o navegador
if /i "%~1"=="oculto" (
  "%PY%" -m streamlit run app.py --browser.gatherUsageStats false --server.address localhost --server.port 8501 --server.headless true
  popd
  exit /b 0
)

echo.
echo Abrindo o programa no navegador... (feche esta janela para encerrar)
"%PY%" -m streamlit run app.py --browser.gatherUsageStats false --server.address localhost --server.port 8501
popd
pause
