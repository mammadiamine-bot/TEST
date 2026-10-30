@echo off
setlocal
title Claude IT Technician WORKER - manual mode (visible)
set "EXE=C:\CITWork\worker\cit-worker.exe"
set "DATA=C:\CITWork\worker\data"
if not exist "%EXE%" goto :missing
echo ================================================================
echo  Claude IT Technician WORKER - MANUAL MODE
echo  It runs only while this window is open. No startup task exists.
echo  Close this window or press Ctrl+C to stop it.
echo  Create a file named STOP in %DATA% to lock it immediately.
echo ================================================================
echo.
echo Find this PC's private LAN address with:  ipconfig   (IPv4 Address of Wi-Fi/Ethernet)
set "BIND="
set /p BIND=Private LAN IPv4 address to listen on:
if "%BIND%"=="" goto :nobind
echo.
echo Windows Firewall may ask about cit-worker.exe: tick PRIVATE networks only.
echo Then, in a SECOND window, run enroll-worker.bat to get the one-time code.
echo.
"%EXE%" --data-dir "%DATA%" run --bind %BIND% --port 8777
echo.
pause
exit /b 0
:missing
echo %EXE% not found. Run install-worker.bat from the USB drive first.
pause
exit /b 1
:nobind
echo No address entered. Nothing was started.
pause
exit /b 1
