@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title FlowXav

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo.
echo   ==============================================
echo     FlowXav - guidage interactif du cockpit
echo   ==============================================
echo.

if exist "%PY%" goto :ready

echo   Premiere execution : installation de l'environnement.
set "BOOTSTRAP="
py -3 --version >nul 2>&1 && set "BOOTSTRAP=py -3"
if not defined BOOTSTRAP (
  python --version >nul 2>&1 && set "BOOTSTRAP=python"
)
if not defined BOOTSTRAP (
  echo   [ERREUR] Python introuvable. Installez Python 3.11 ou plus recent.
  goto :fail
)
%BOOTSTRAP% -m venv "%VENV%" || goto :fail
"%PY%" -m pip install --disable-pip-version-check --upgrade "pip>=26.2" || goto :fail
"%PY%" -m pip install --disable-pip-version-check -e ".[dev]" || goto :fail

:ready
"%PY%" -m flowxav.desktop %*
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo   FlowXav n'a pas pu demarrer. Journal : data\logs\flowxav.log
pause
exit /b 1
