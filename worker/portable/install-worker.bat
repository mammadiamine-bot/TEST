@echo off
setlocal
title Claude IT Technician WORKER - copy files (manual mode)
set "DEST=C:\CITWork\worker"
echo ================================================================
echo  This copies cit-worker.exe from this USB drive to %DEST%
echo  It installs nothing, creates NO startup/scheduled task, changes
echo  NO firewall or security setting, and never overwrites a file.
echo ================================================================
if not exist "%~dp0cit-worker.exe" goto :nosrc
if not exist "%~dp0cit-worker.exe.sha256" goto :nosrc
if exist "%DEST%\cit-worker.exe" goto :exists
if not exist "C:\CITWork" mkdir "C:\CITWork"
if not exist "%DEST%" mkdir "%DEST%"
copy "%~dp0cit-worker.exe" "%DEST%\cit-worker.exe" >nul
if errorlevel 1 goto :copyfail
set "EXPECT="
set /p EXPECT=<"%~dp0cit-worker.exe.sha256"
set "GOT="
for /f "skip=1 tokens=*" %%h in ('certutil -hashfile "%DEST%\cit-worker.exe" SHA256') do if not defined GOT set "GOT=%%h"
set "GOT=%GOT: =%"
if /i "%GOT%"=="%EXPECT%" goto :ok
echo.
echo HASH MISMATCH - do NOT use this copy.
echo   expected: %EXPECT%
echo   actual:   %GOT%
pause
exit /b 1
:ok
echo.
echo OK: copied and SHA-256 verified:
echo   %GOT%
echo Next: double-click run-worker.bat on this USB drive.
pause
exit /b 0
:exists
echo %DEST%\cit-worker.exe already exists. Nothing was changed.
echo Rename or remove it yourself if you want to replace it.
pause
exit /b 1
:nosrc
echo cit-worker.exe or cit-worker.exe.sha256 is missing next to this script.
pause
exit /b 1
:copyfail
echo Copy failed. Nothing was installed.
pause
exit /b 1
