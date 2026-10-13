@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call start_windows.bat
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --add-data "assets;assets" --add-data "tools/windows_iso.ps1;tools" --add-data "LICENSE;." --add-data "THIRD_PARTY_NOTICES.md;." --name "DiscForge_RU" main.py
if errorlevel 1 (echo Не удалось собрать exe & pause & exit /b 1)
if exist "runtime\java\bin\server\jvm.dll" xcopy "runtime\java" "dist\DiscForge_RU\runtime\java\" /E /I /Y >nul
if not exist "dist\DiscForge_RU\runtime\java\bin\server\jvm.dll" echo Для кнопки VLC поместите Java 8 x64 JRE с лицензиями в dist\DiscForge_RU\runtime\java.
echo EXE готов: dist\DiscForge_RU\DiscForge_RU.exe
pause
