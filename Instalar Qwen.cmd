@echo off
cd /d "%~dp0"
py -3.12 tools\install_qwen.py
if errorlevel 1 (
    echo.
    echo Falha ao instalar o Qwen TTS. Veja a mensagem acima.
    pause
    exit /b 1
)
echo.
pause
