@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   RVC Cover Kit  -  mix.bat
echo   Re-mix volume only. No separation, no voice conversion.
echo ============================================================
echo.

if "%~1"=="" (
    echo Usage: drag the original song, or a finished cover, onto this file
    echo        It must have been run through cover.bat at least once
    echo.
    pause
    exit /b
)

set "SONG=%~1"
echo Target : %SONG%
echo.

set "VG="
set /p VG=Vocal gain in dB [Enter = keep last value]: 
set "IG="
set /p IG=Instrumental gain in dB [Enter = keep last value]: 

set "ARGS=mix.py "%SONG%""
if defined VG set "ARGS=%ARGS% --vocal-gain %VG%"
if defined IG set "ARGS=%ARGS% --inst-gain %IG%"

echo.
echo Re-mixing...

if exist "%~dp0.venv\Scripts\python.exe" goto :run_venv
if exist "%~dp0runtime\python.exe" goto :run_runtime
goto :run_path

:run_venv
"%~dp0.venv\Scripts\python.exe" %ARGS%
goto :done

:run_runtime
"%~dp0runtime\python.exe" %ARGS%
goto :done

:run_path
py -3 %ARGS%
if errorlevel 9009 python %ARGS%
goto :done

:done
echo.
echo ------------------------------------------------------------
echo Output goes to the "output" folder, filename ends with _mix_v-3_i-6
echo.
pause
