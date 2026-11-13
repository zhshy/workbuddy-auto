@echo off
chcp 65001 >nul
setlocal

REM 用 WorkBuddy 客户端自己的 Node 运行时执行提取脚本。
REM 必须走 WorkBuddy.exe，因为解密密钥只能从客户端的 native binding 取到。

set "WB_EXE=C:\Program Files\WorkBuddy\WorkBuddy.exe"
if not exist "%WB_EXE%" (
  echo [x] 没找到 WorkBuddy.exe，请确认安装路径，改本文件的 WB_EXE 变量。
  pause
  exit /b 1
)

set ELECTRON_RUN_AS_NODE=1
"%WB_EXE%" "%~dp0extract-token.js"
set RC=%ERRORLEVEL%
echo.
if %RC%==0 (
  echo 接下来可以执行：
  echo     python push-secrets.py ^<你的GitHub用户名^>/^<仓库名^>
) else (
  echo [x] 提取失败，错误码 %RC%。请确认 WorkBuddy 客户端正在运行且已登录。
)
pause
