@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  py -3.11 -m venv .venv
  if errorlevel 1 (echo Could not create venv. Install Python 3.11+ and try again.&pause&exit /b 1)
)
call ".venv\Scripts\activate.bat"
python -m pip install -r backend\requirements.txt
if errorlevel 1 (echo Dependency installation failed.&pause&exit /b 1)

set "GCC=gcc"
where gcc >nul 2>&1
if errorlevel 1 if exist "C:\msys64\ucrt64\bin\gcc.exe" set "GCC=C:\msys64\ucrt64\bin\gcc.exe"

"%GCC%" --version >nul 2>&1
if errorlevel 1 (
  echo GCC not found. Install MSYS2 UCRT64 GCC or add gcc to PATH.
  pause
  exit /b 1
)

echo Compiling C DSA bridge programs...
"%GCC%" C_DSA\searching.c -o C_DSA\search_station.exe
if errorlevel 1 goto c_error
"%GCC%" C_DSA\sorting.c -o C_DSA\sorting.exe
if errorlevel 1 goto c_error
"%GCC%" C_DSA\queue.c -o C_DSA\queue.exe
if errorlevel 1 goto c_error

if not exist ".env" (
  copy /Y .env.example .env >nul
  echo.
  echo IMPORTANT: Open .env and paste your MongoDB URI, then run run.bat again.
  pause
  exit /b 0
)

python backend\seed_stations.py
if errorlevel 1 (
  echo MongoDB connection/seed failed. Check .env and Atlas network access.
  pause
  exit /b 1
)

start "ChargeEase Flask" cmd /k ".venv\Scripts\python.exe backend\app.py"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:5000/
exit /b 0

:c_error
echo C compilation failed. Make sure UCRT64 GCC is installed.
pause
exit /b 1
