@echo off
setlocal
title Claude IT Technician WORKER - LOCK / REVOKE
set "EXE=C:\CITWork\worker\cit-worker.exe"
set "DATA=C:\CITWork\worker\data"
if not exist "%EXE%" goto :missing
echo This stops all running jobs and makes the worker refuse everything (lock).
choice /m "Lock the worker now"
if errorlevel 2 goto :skiplock
"%EXE%" --data-dir "%DATA%" lock
:skiplock
echo.
echo Revoking removes the technician PC's credential: it must enroll again with a new code.
choice /m "Revoke ALL controller credentials too"
if errorlevel 2 goto :done
"%EXE%" --data-dir "%DATA%" revoke-all
:done
echo.
pause
exit /b 0
:missing
echo %EXE% not found.
pause
exit /b 1
