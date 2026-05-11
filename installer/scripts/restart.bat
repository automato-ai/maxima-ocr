@echo off
REM Restart the Maxima OCR Modbus service. Must be run as administrator.
sc stop MaximaOCR >nul 2>&1

REM Wait for the service to fully stop. Polls "sc query" for STOPPED state.
set /a _tries=0
:wait_stopped
sc query MaximaOCR | findstr /C:"STATE" | findstr /C:"STOPPED" >nul
if %errorlevel%==0 goto start
set /a _tries+=1
if %_tries% geq 30 (
    echo.
    echo MaximaOCR did not reach STOPPED state within 30s. Aborting restart.
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto wait_stopped

:start
sc start MaximaOCR
if errorlevel 1 (
    echo.
    echo Could not start MaximaOCR service after stopping.
    exit /b 1
)
exit /b 0
