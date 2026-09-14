@echo off
rem ============================================================================
rem  create_backup.bat - launcher only.
rem  All logic lives in "create_backup.ps1", all configuration
rem  lives in ".env" (template: ".env.example") - nothing is configured here.
rem  --------------------------------------------------------------------------
rem  Double-click this file to run interactively. For scheduled runs (Task
rem  Scheduler / cron) call the PowerShell script directly with "--no-pause":
rem    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_backup.ps1" --no-pause
rem ============================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_backup.ps1" %*