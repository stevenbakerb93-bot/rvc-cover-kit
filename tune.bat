@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   RVC Cover Kit  -  tune.bat
echo   Try parameter sets on a short clip instead of the whole song
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

set "START=60"
set /p START=Clip start in seconds [Enter = 60, pick a chorus]: 
if not defined START set "START=60"

set "DUR=20"
set /p DUR=Clip length in seconds [Enter = 20]: 
if not defined DUR set "DUR=20"

echo.
echo Tunable keys: index_rate  protect  rms_mix  transpose  pitch  f0_method
set "KEY=index_rate"
set /p KEY=Key to scan [Enter = index_rate]: 
if not defined KEY set "KEY=index_rate"

set "VALS=0.3,0.5,0.7"
if /i "%KEY%"=="transpose" set "VALS=-2,-1,0"
if /i "%KEY%"=="pitch" set "VALS=-2,0,2"
if /i "%KEY%"=="rms_mix" set "VALS=0.25,0.5,0.75"
if /i "%KEY%"=="f0_method" set "VALS=rmvpe,fcpe,harvest"
if /i "%KEY%"=="protect" set "VALS=0.25,0.33,0.40"
set /p VALS=Values, comma separated [Enter = %VALS%]: 

rem only suggest feature retrieval when a matching .index exists
set "RATE=0"
if exist "assets\indices\%MODEL:.pth=.index%" set "RATE=0.5"

echo.
echo ------------------------------------------------------------
echo   Input  : %SONG%
echo   Model  : %MODEL%
echo   Clip   : from %START%s, %DUR%s long
echo   Scan   : %KEY% = %VALS%
echo ------------------------------------------------------------
echo.
echo First run separates stems (about 90s). The same song is cached after that,
echo so each later parameter set takes about 10 seconds.
echo.

set "ARGS=tune.py "%SONG%" --model "%MODEL%" --start %START% --dur %DUR% --index-rate %RATE% --vary %KEY%=%VALS%"

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
echo Clips go to the "tune" folder inside the RVC root.
echo Pick the best one, then run cover.bat with those values.
echo.
pause
