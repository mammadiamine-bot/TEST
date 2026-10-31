@echo off
rem One-click start for the Claude IT Technician on this PC. Double-click me.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cit-supervisor.ps1"
if errorlevel 1 pause
