@echo off
cd /d "%~dp0"
if not exist ".env" (
  echo Missing .env file.
  echo Copy .env.example to .env, add your NEW valid OPENAI_API_KEY, then run this file again.
  pause
  exit /b 1
)
python app.py
if errorlevel 1 pause
