@echo off
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 "%~dp0bugkb.py" %*
) else (
  python "%~dp0bugkb.py" %*
)
exit /b %errorlevel%
