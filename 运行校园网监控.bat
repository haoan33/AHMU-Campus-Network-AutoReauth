@echo off
chcp 65001 >nul
title Campus Network Monitor
cd /d "%~dp0"
python "%~dp0campus_carrier_monitor.py"
pause
