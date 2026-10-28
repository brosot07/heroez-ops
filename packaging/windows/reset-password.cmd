@echo off
rem Reset the Heroez Ops owner password: writes a new random initial password that the
rem app adopts at the next login page load. Needs a Windows administrator (asks for elevation).
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
setlocal
for /f "delims=" %%p in ('powershell -NoProfile -Command "-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 12 | ForEach-Object {[char]$_})"') do set NEWPW=%%p
> "%ProgramData%\HeroezOps\initial_password.txt" echo %NEWPW%
echo.
echo Password pemilik baru: %NEWPW%
echo.
echo Buka http://127.0.0.1:8788 dan masuk dengan password ini, lalu ganti di bagian "Ganti password pemilik".
echo.
start "" "http://127.0.0.1:8788/login"
pause
