@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ShowRunner.spec
if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\ShowRunner\ShowRunner.exe
echo Copy the whole dist\ShowRunner folder to the target Windows machine.
endlocal
