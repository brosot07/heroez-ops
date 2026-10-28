@echo off
rem Reset the Heroez Ops owner password. Needs a Windows administrator (asks for elevation).
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
echo.> "%ProgramData%\HeroezOps\reset_password"
echo.
echo Password pemilik dihapus. Buka http://127.0.0.1:8788 untuk membuat password baru.
echo.
start "" "http://127.0.0.1:8788/login"
pause
