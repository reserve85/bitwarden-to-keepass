@echo off
rem ============================================================================
rem  create_backup.bat - launcher only.
rem  All logic lives in "create_backup.ps1", all configuration
rem  lives in ".env" (template: ".env.example") - nothing is configured here.
rem  --------------------------------------------------------------------------
rem  Double-click this file to run interactively: if Bitwarden credentials are
rem  not configured in ".env" you will be prompted for your email, master
rem  password, 2FA code (if enabled) and the KeePass database password - nothing
rem  needs to be stored in ".env". If credentials ARE configured, the export
rem  runs unattended instead.
rem  For scheduled runs (Task Scheduler / cron) call the PowerShell script
rem  directly with "--no-pause":
rem    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_backup.ps1" --no-pause
rem ============================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_backup.ps1" %*