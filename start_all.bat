@echo off
REM Starts Portfolio 1, Portfolio 2, and the frontend, each in its own window.
REM Double-click this file (or run it from a terminal) any time you need to
REM bring everything back up — no need to type each command by hand.

echo Starting Portfolio 1 (10L, port 8000)...
start "MoneyMaker - Portfolio 1" cmd /k "python main.py --paper"

timeout /t 3 /nobreak >nul

echo Starting Portfolio 2 (5L, port 8001)...
start "MoneyMaker - Portfolio 2" cmd /k "set MM_CONFIG_PATH=config.portfolio2.toml && set MM_DATA_DIR=data/portfolios/portfolio2 && python main.py --paper"

timeout /t 3 /nobreak >nul

echo Starting frontend (localhost:3000)...
start "MoneyMaker - Frontend" cmd /k "cd ui && npm run dev"

echo.
echo All three started in separate windows. Close this window any time —
echo the three it opened will keep running on their own.
