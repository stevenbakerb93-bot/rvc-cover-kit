@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   RVC Cover Kit  -  cover.bat
echo ============================================================
echo.

if "%~1"=="" (
    echo Usage: drag an audio file - mp3, wav, flac or m4a - onto this file
    echo.
    pause
    exit /b
)

set "SONG=%~1"
echo Input : %SONG%
echo.

if not exist "assets\weights" (
    echo [ERROR] folder assets\weights not found
    echo         Put this .bat in the RVC project root folder
    pause
    exit /b 1
)

echo Available voice models:
set "FIRST="
for %%f in ("assets\weights\*.pth") do (
    echo    %%~nxf
    if not defined FIRST set "FIRST=%%~nxf"
)
if not defined FIRST (
    echo    [none]
    echo.
    echo [ERROR] no .pth model in assets\weights
    echo         Copy your voice model there, or train one in the WebUI
    pause
    exit /b 1
)
echo.

set "MODEL="
set /p MODEL=Model file [Enter = %FIRST%]: 
if not defined MODEL set "MODEL=%FIRST%"
if not exist "assets\weights\%MODEL%" (
    echo.
    echo [ERROR] assets\weights\%MODEL% not found
    pause
    exit /b 1
)

rem only suggest feature retrieval when a matching .index exists
set "DEFRATE=0"
if exist "assets\indices\%MODEL:.pth=.index%" set "DEFRATE=0.5"

set "RATE="
set /p RATE=Feature retrieval ratio 0-1 [Enter = %DEFRATE%, use 0 if no .index]: 
if not defined RATE set "RATE=%DEFRATE%"

set "TR="
set /p TR=Transpose whole song in semitones [Enter = 0]: 
if not defined TR set "TR=0"

set "VG="
set /p VG=Vocal gain in dB [Enter = -3]: 
if not defined VG set "VG=-3"

set "IG="
set /p IG=Instrumental gain in dB [Enter = 0]: 
if not defined IG set "IG=0"

echo.
echo ------------------------------------------------------------
echo   Input      : %SONG%
echo   Model      : %MODEL%
echo   Transpose  : %TR%    Index ratio: %RATE%
echo   Mix        : vocal %VG% dB, instrumental %IG% dB
echo ------------------------------------------------------------
echo.
echo About 2 minutes for a 4-minute song. Close the WebUI first to save VRAM.
echo Stems are kept, so later volume tweaks only need mix.bat - a few seconds.
echo.

set "ARGS=cover.py "%SONG%" --model "%MODEL%" --transpose %TR% --index-rate %RATE% --vocal-gain %VG% --inst-gain %IG%"

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
echo Output goes to the "output" folder inside the RVC root.
echo To re-mix volume only, drop the song onto mix.bat
echo.
pause
