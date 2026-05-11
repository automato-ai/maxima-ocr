@echo off
REM Stop and remove the MaximaOCR Windows service. Must be run as administrator.
REM Invoked by the uninstaller; can also be run manually.

setlocal
set NSSM=%~dp0nssm.exe
set SVC=MaximaOCR

sc stop %SVC% >nul 2>&1
if exist "%NSSM%" (
    "%NSSM%" stop %SVC% >nul 2>&1
    "%NSSM%" remove %SVC% confirm >nul 2>&1
) else (
    sc delete %SVC% >nul 2>&1
)

endlocal
exit /b 0
