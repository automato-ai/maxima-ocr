@echo off
REM Thin wrapper around build.ps1. All build logic lives in PowerShell so the
REM same script runs identically on a dev PC and a GitHub Actions runner.
REM
REM Usage:
REM   compile.bat                Use `git describe --tags` to derive the version.
REM   compile.bat 0.4            Build version 0.4.
REM   compile.bat 0.4 -SkipPyInstaller    Repackage installer without rebuilding exes.
REM
REM In CI: invoke as `compile.bat %GITHUB_REF_NAME%` on a tag push.

if "%~1"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" -Version "%~1" %2 %3 %4 %5 %6 %7 %8 %9
)
exit /b %ERRORLEVEL%
