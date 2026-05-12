@echo off
REM Register MaximaOCR as a Windows service via NSSM. Must be run as administrator.
REM Safe to re-run: stops and removes any prior registration first.
REM Invoked by the installer; can also be run manually to repair the service.

setlocal
set NSSM=%~dp0nssm.exe
set SVC=MaximaOCR
set INSTALL_DIR=%~dp0
REM Strip trailing backslash so NSSM AppDirectory is clean.
if "%INSTALL_DIR:~-1%"=="\" set INSTALL_DIR=%INSTALL_DIR:~0,-1%

if not exist "%NSSM%" (
    echo nssm.exe not found at %NSSM% -- cannot register service.
    exit /b 1
)
if not exist "%INSTALL_DIR%\modbus_server.exe" (
    echo modbus_server.exe not found in %INSTALL_DIR% -- cannot register service.
    exit /b 1
)

REM Stop and remove if already registered. Errors here are expected on first install.
sc stop %SVC% >nul 2>&1
"%NSSM%" stop %SVC% >nul 2>&1
"%NSSM%" remove %SVC% confirm >nul 2>&1

"%NSSM%" install %SVC% "%INSTALL_DIR%\modbus_server.exe" || goto fail
"%NSSM%" set %SVC% AppDirectory "%INSTALL_DIR%" || goto fail
"%NSSM%" set %SVC% Start SERVICE_AUTO_START || goto fail
"%NSSM%" set %SVC% DisplayName "Maxima OCR Modbus Server" || goto fail
"%NSSM%" set %SVC% Description "OCR recognition service. Exposes a Modbus TCP slave on port 502 for PLC integration." || goto fail
"%NSSM%" set %SVC% AppStdout "%INSTALL_DIR%\service-stdout.log" || goto fail
"%NSSM%" set %SVC% AppStderr "%INSTALL_DIR%\service-stderr.log" || goto fail
"%NSSM%" set %SVC% AppRotateFiles 1 || goto fail
"%NSSM%" set %SVC% AppRotateBytes 1048576 || goto fail
"%NSSM%" start %SVC% || goto fail

endlocal
exit /b 0

:fail
echo Service registration failed.
endlocal
exit /b 1
