@echo off
chcp 65001 >nul
title Test Reauth
cd /d "%~dp0"
python "%~dp0test_reauth.py"
pause
