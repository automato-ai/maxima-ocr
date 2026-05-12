@echo off
REM Run the Maxima OCR uninstaller. Must be run as administrator.
REM Forwards any args to the Inno-generated uninstaller, so you can pass
REM /VERYSILENT, /SILENT, /NORESTART, /LOG=..., etc.
REM
REM Examples:
REM   uninstall.bat                      Interactive uninstall (prompts).
REM   uninstall.bat /VERYSILENT          Headless uninstall, no UI.
REM   uninstall.bat /SILENT              Progress only, no questions.

set "UNINSTALLER=%~dp0unins000.exe"
if not exist "%UNINSTALLER%" (
    echo.
    echo Uninstaller not found at %UNINSTALLER%.
    echo This script only works inside an installed copy of Maxima OCR.
    exit /b 1
)

"%UNINSTALLER%" %*
exit /b %ERRORLEVEL%
