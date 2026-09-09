@echo off
REM Instala el scheduler local de Mapache como tarea de Windows Task Scheduler.
REM Ejecutar como administrador no es necesario para tareas del usuario actual.

set TASK_NAME=MapacheScraplingScheduler
set BACKEND_DIR=%~dp0..
set PY_EXE=python

REM Resuelve python del PATH (o del venv del backend si existe)
if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" set PY_EXE=%BACKEND_DIR%\.venv\Scripts\python.exe

REM Elimina la tarea si ya existe
schtasks /Query /TN %TASK_NAME% >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Eliminando tarea previa %TASK_NAME%...
    schtasks /Delete /TN %TASK_NAME% /F >nul
)

REM Registra: cada 30 min, al iniciar sesión, corre un ciclo del scraper
schtasks /Create /TN %TASK_NAME% /SC MINUTE /MO 30 ^
  /TR "\"%PY_EXE%\" \"%BACKEND_DIR%\app\scheduler_local.py\" --once" ^
  /F
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: no se pudo registrar la tarea.
    exit /b 1
)

echo.
echo Tarea %TASK_NAME% registrada.
echo   - Cada 30 minutos corre un ciclo de scraping (python scheduler_local.py --once)
echo   - Logs: Task Scheduler > Historial
echo Para iniciar sesión también: schtasks /Run /TN %TASK_NAME%
echo Para eliminar: schtasks /Delete /TN %TASK_NAME% /F
pause
