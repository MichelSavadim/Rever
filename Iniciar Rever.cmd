@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente Python nao encontrado.
    echo Consulte o README.md para instalar as dependencias.
    pause
    exit /b 1
)

if not exist "logs" mkdir "logs"
".venv\Scripts\python.exe" -m pdf_reader >> "logs\console.log" 2>&1
if errorlevel 1 (
    echo.
    echo O aplicativo encerrou com erro.
    echo Veja o arquivo crash_DATA_HORA.log mais recente em logs e logs\console.log.
    pause
)
