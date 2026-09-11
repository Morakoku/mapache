@echo off
REM ============================================================
REM VeyraWarmupDaily — tarea diaria del warm-up de dominio
REM Ejecuta warmup_resend.py --send con el venv de Hermes.
REM Programada 09:05 diario como VeyraWarmupDaily (DISABLED por
REM defecto hasta que RESEND_API_KEY este en backend\.env).
REM Habilitar:  schtasks /Change /TN VeyraWarmupDaily /ENABLE
REM Deshabilitar: schtasks /Change /TN VeyraWarmupDaily /DISABLE
REM ============================================================

setlocal

set "PYTHON=C:\Users\edwin\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
set "SCRIPT=C:\Users\edwin\Documents\Trinidad\mapache\backend\scripts\warmup_resend.py"
set "ENV_FILE=C:\Users\edwin\Documents\Trinidad\mapache\backend\.env"

if not exist "%PYTHON%" (
    echo [VEYRA-WARMUP] ERROR: no existe el interprete %PYTHON%
    exit /b 1
)
if not exist "%SCRIPT%" (
    echo [VEYRA-WARMUP] ERROR: no existe el script %SCRIPT%
    exit /b 1
)
if not exist "%ENV_FILE%" (
    echo [VEYRA-WARMUP] ERROR: no existe %ENV_FILE%
    exit /b 1
)

REM --send falla con exit 1 y mensaje claro si falta RESEND_API_KEY;
REM el log queda en warmup_resend.log junto al script.
REM 1) Suprime rebotes/quejas antes de enviar (protege la reputacion del dominio).
"%PYTHON%" "%SCRIPT%" --check-bounces --env "%ENV_FILE%"
if errorlevel 1 (
    echo [VEYRA-WARMUP] AVISO: la revision de rebotes fallo; se continua con el envio.
)

REM 2) Envia el cupo del dia.
"%PYTHON%" "%SCRIPT%" --send --env "%ENV_FILE%"
exit /b %ERRORLEVEL%
