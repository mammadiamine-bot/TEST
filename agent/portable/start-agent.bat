@echo off
setlocal
title Claude IT Technician agent - visible session
echo ================================================================
echo  Claude IT Technician - support agent
echo  Nothing is installed. It only runs while this window is open.
echo  Close this window or press Ctrl+C to disconnect immediately.
echo ================================================================
echo.
set ARGS=
set /p ROOT=Project folder the technician may work in (empty = diagnostics only): 
if not "%ROOT%"=="" (
  if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%
  set ARGS=--root "%ROOT%"
  choice /m "Allow running commands inside that folder"
  if errorlevel 1 if not errorlevel 2 set ARGS=%ARGS% --allow-terminal
  choice /m "Allow creating/changing/deleting files inside that folder"
  if errorlevel 1 if not errorlevel 2 set ARGS=%ARGS% --allow-write
  choice /m "Allow Claude Code to run on THIS computer (uses this computer's own Claude login)"
  if errorlevel 1 if not errorlevel 2 set ARGS=%ARGS% --allow-claude
)
choice /m "Allow Windows repair tools (restart services, clean temp files...)"
if errorlevel 1 if not errorlevel 2 set ARGS=%ARGS% --allow-repair
set /p MIN=Session length in minutes (default 30): 
if not "%MIN%"=="" set ARGS=%ARGS% --minutes %MIN%
echo.
"%~dp0cit-agent.exe" %ARGS%
echo.
pause
