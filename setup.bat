@echo off
setlocal EnableDelayedExpansion

echo ============================================
echo  OpenSooq Scraper - Automated Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please download and install Python from https://python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PY_VER=%%i
echo [OK] Found %PY_VER%

:: Check pip
pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip not found. Reinstall Python and ensure pip is included.
    pause
    exit /b 1
)
echo [OK] pip is available

:: Check git
git --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] git not found - skipping repo clone check.
) else (
    for /f "tokens=*" %%i in ('git --version') do echo [OK] %%i
)

echo.
echo [1/4] Creating virtual environment...
if exist venv (
    echo       Already exists, skipping.
) else (
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo       Created.
)

echo.
echo [2/4] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    echo Try running this in PowerShell first:
    echo   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
    pause
    exit /b 1
)
echo       Activated.

echo.
echo [3/4] Installing Python dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo       Done.

echo.
echo [4/4] Installing Playwright Chromium browser...
playwright install chromium
if errorlevel 1 (
    echo [ERROR] Failed to install Chromium.
    pause
    exit /b 1
)
echo       Done.

echo.
echo ============================================
echo  Setup complete! Running test scrape...
echo  (5 pages, text only - no images)
echo ============================================
echo.

python scraper.py --pages 5 --no-images

echo.
echo ============================================
echo  Done. Check listings.csv and listings.json
echo ============================================
pause
