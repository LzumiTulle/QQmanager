@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
where py >nul 2>nul
if errorlevel 1 goto python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto deps
:python
python -m venv .venv
if errorlevel 1 goto failed
:deps
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
:run
.venv\Scripts\python.exe -B run.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo 启动失败。请确认已安装 Python 3.9 或更新版本，并检查上方错误。
pause
exit /b 1
