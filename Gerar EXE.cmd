@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente Python nao encontrado. Consulte o README.md.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller nao encontrado. Instale com:
    echo .venv\Scripts\python.exe -m pip install pyinstaller
    pause
    exit /b 1
)

".venv\Scripts\python.exe" build_rever.py

if errorlevel 1 (
    echo Falha ao gerar o executavel.
    pause
    exit /b 1
)

echo.
echo Executavel gerado em Rever.exe
pause
