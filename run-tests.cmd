@echo off
setlocal
cd /d "%~dp0"
set "SSVEP_TEST_PYTHON=%SSVEP_DECODER_PYTHON%"
if not defined SSVEP_TEST_PYTHON if exist ".venv\Scripts\python.exe" set "SSVEP_TEST_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined SSVEP_TEST_PYTHON if exist "%USERPROFILE%\.conda\envs\venv\python.exe" set "SSVEP_TEST_PYTHON=%USERPROFILE%\.conda\envs\venv\python.exe"
if not defined SSVEP_TEST_PYTHON set "SSVEP_TEST_PYTHON=python.exe"
"%SSVEP_TEST_PYTHON%" -m ssvep_decoding.launcher
if errorlevel 1 pause
