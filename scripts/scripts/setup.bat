@echo off
REM ASTRA BOT - quick setup for Windows PowerShell
REM Run from project root.

where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.11 from https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist .venv (
    echo ==^> Creating venv
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo ==^> Installing dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install python-dotenv

if not exist .env (
    copy .env.example .env >nul
    echo.
    echo Created .env from .env.example.
    echo Open .env in Notepad and fill in OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSPHRASE.
    notepad .env
)

echo.
echo ==^> Running tests
python -m pytest -q

echo.
echo Done. Next:
echo   python scripts\test_okx.py
echo   python scripts\train_multi_timeframe.py --days 1095
echo   python scripts\run_paper.py
pause
