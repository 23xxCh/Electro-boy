@echo off
REM ============================================================
REM  One-click start VE + Controller_LP nodes.
REM  VE only:  launch.bat --no-controller
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if defined CONDA_BASE (
    call "%CONDA_BASE%\Scripts\activate.bat" env_ankerproject
) else (
    call conda activate env_ankerproject
)
if errorlevel 1 (
    echo [ERROR] cannot activate env_ankerproject. Install/init conda, or set CONDA_BASE.
    pause
    exit /b 1
)

python launch.py %*
pause
