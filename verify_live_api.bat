@echo off
cd /d "%~dp0"
python verify_live_api.py
if errorlevel 1 pause
