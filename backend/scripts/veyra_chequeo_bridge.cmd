@echo off
rem Puente Chequeo Express -> WhatsApp oficial de Veyra (Hermes send)
set "PYTHONIOENCODING=utf-8"
"C:\Users\edwin\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" "C:\Users\edwin\Documents\Trinidad\mapache\backend\scripts\veyra_chequeo_bridge.py"
exit /b %errorlevel%
