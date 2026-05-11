@echo off
REM Stop the Maxima OCR Modbus service. Must be run as administrator.
sc stop MaximaOCR
if errorlevel 1 (
    echo.
    echo Could not stop MaximaOCR service.
    echo If the service is not installed, run register-service.bat from this folder.
    exit /b 1
)
exit /b 0
