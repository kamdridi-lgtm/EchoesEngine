@echo off
setlocal
set "ROOT=%~dp0"
set "INSTALLER=%ROOT%scripts\install-echoes-stem-review-update.ps1"

if not exist "%INSTALLER%" (
  echo.
  echo ECHOES STEM REVIEW PACKAGE ERROR
  echo Extract the complete ZIP before running this file.
  echo Missing: %INSTALLER%
  echo.
  pause
  exit /b 2
)

echo ================================================================
echo   ECHOES STEM REVIEW - INSTALL UPDATE AND OPEN REVIEW GATE
echo ================================================================

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%" -SourceRoot "%ROOT%"
set "INSTALL_EXIT=%ERRORLEVEL%"
if not "%INSTALL_EXIT%"=="0" (
  echo.
  echo Echoes Stem Review installation failed with code %INSTALL_EXIT%.
  pause
  exit /b %INSTALL_EXIT%
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\A.I\EchoesStemRuntime\Review-Echoes-Stem.ps1" -Interactive
set "REVIEW_EXIT=%ERRORLEVEL%"

echo.
if "%REVIEW_EXIT%"=="0" (
  echo Echoes stem listening review completed.
) else (
  echo Echoes stem listening review stopped or was blocked with code %REVIEW_EXIT%.
)
echo Review evidence: D:\A.I\EchoesControl\Echoes-Stem-Review-Control-Latest.zip
pause
exit /b %REVIEW_EXIT%
