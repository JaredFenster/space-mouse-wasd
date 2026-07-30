@echo off
rem Launch the SpaceMouse WASD controller UI (no console window).
start "" /d "%~dp0" pythonw "%~dp0controller\spacemouse_wasd.py"
