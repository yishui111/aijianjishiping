@echo off
rem ============================================================
rem  AI视频工场 - 新机一键装环境(创建 venv 并安装三个服务依赖)
rem  之后运行 start_local.ps1 启动;模型类大件见 DEPLOY.md
rem ============================================================
setlocal cd /d "%~dp0"
where python >nul 2>nul || (echo [ERROR] 未找到 python,请先安装 Python 3.10+ & pause & exit /b 1)
if not exist "runtime\venv\Scripts\python.exe" (
  echo Creating runtime\venv ...
  python -m venv runtime\venv || (echo [ERROR] venv 创建失败 & pause & exit /b 1)
)
for %%s in (planner executor analyzer) do (
  echo Installing deps for %%s ...
  runtime\venv\Scripts\python.exe -m pip install -r services\%%s\requirements.txt || echo [WARN] %%s 依赖安装有报错,继续
)
echo.
echo 完成。启动: powershell -ExecutionPolicy Bypass -File start_local.ps1
pause
