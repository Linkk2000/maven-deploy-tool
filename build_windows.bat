@echo off
setlocal

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=py
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Python not found in PATH.
        exit /b 1
    )
    set PYTHON_CMD=python
)

echo [INFO] Using Python command: %PYTHON_CMD%
echo [INFO] Installing or upgrading PyInstaller...
%PYTHON_CMD% -m pip install --upgrade pyinstaller
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install PyInstaller.
    exit /b 1
)

if exist "build\pyinstaller-windows" rmdir /s /q "build\pyinstaller-windows"
if exist "build\spec-windows" rmdir /s /q "build\spec-windows"
if exist "dist\windows" rmdir /s /q "dist\windows"

echo [INFO] Building Windows executable...
%PYTHON_CMD% -m PyInstaller ^
  --clean ^
  --onefile ^
  --name maven-push-tool ^
  --distpath "dist\windows" ^
  --workpath "build\pyinstaller-windows" ^
  --specpath "build\spec-windows" ^
  "push_maven_local.py"

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Build failed.
    exit /b 1
)

echo [INFO] Build completed: dist\windows\maven-push-tool.exe
exit /b 0
