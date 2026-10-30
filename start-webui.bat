@echo off
setlocal
cd /d "%~dp0"
set "GRADIO_ANALYTICS_ENABLED=False"
set "NO_PROXY=localhost,127.0.0.1,::1"
set "PYTHONIOENCODING=utf-8"

set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0runtime\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo Starting RVC WebUI...
echo Python: %PYEXE%
echo URL:    http://127.0.0.1:7897
echo.

"%PYEXE%" webui.py --pycmd "%PYEXE%" --port 7897

echo.
echo WebUI exited with code %ERRORLEVEL%
pause
