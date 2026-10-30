@echo off
setlocal
title Claude IT Technician WORKER - enrollment code
set "EXE=C:\CITWork\worker\cit-worker.exe"
set "DATA=C:\CITWork\worker\data"
if not exist "%EXE%" goto :missing
echo Printing a ONE-TIME enrollment code (valid 10 minutes, single use, 5 wrong tries cancel it).
echo Read it to the technician together with the Fingerprint shown in the worker window.
echo.
"%EXE%" --data-dir "%DATA%" enroll
echo.
pause
exit /b 0
:missing
echo %EXE% not found. Run install-worker.bat first.
pause
exit /b 1
