@echo off
REM ============================================================
REM  Package VE into exe (onedir folder) -> dist\VE_Anker\
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if defined CONDA_BASE (
    call "%CONDA_BASE%\Scripts\activate.bat" env_ankerproject
) else (
    call conda activate env_ankerproject
)
if errorlevel 1 (
    echo [ERROR] cannot activate env_ankerproject.
    pause
    exit /b 1
)

pip install --disable-pip-version-check pyinstaller
if errorlevel 1 (
    echo [ERROR] pyinstaller install failed.
    pause
    exit /b 1
)

python _pack.py VE
if errorlevel 1 (
    echo [ERROR] build failed.
    pause
    exit /b 1
)

echo.
echo Build done: ..\VE\dist\VE_Anker\VE_Anker.exe
pause
