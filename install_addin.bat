@echo off
rem Install (or update) the Fusion add-in for the current user.
set "DEST=%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\SpaceMouseWASD"
xcopy /e /i /y "%~dp0addin\SpaceMouseWASD" "%DEST%" >nul
if errorlevel 1 (
    echo Install failed - is Fusion installed for this user?
) else (
    echo Add-in installed to:
    echo   %DEST%
    echo.
    echo Restart Fusion ^(or Shift+S ^> Add-Ins ^> SpaceMouseWASD ^> Run^).
)
pause
