@echo off
REM Start the Maxima OCR Modbus service. Must be run as administrator.
sc start MaximaOCR
if errorlevel 1 (
    echo.
    echo Could not start MaximaOCR service.
    echo If the service is not installed, run register-service.bat from this folder.
    exit /b 1
)
exit /b 0
