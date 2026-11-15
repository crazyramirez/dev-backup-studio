@echo off
rem Abre DEV Backup Studio sin ventana de consola
python -c "import PyQt6" 2>nul || python -m pip install -r "%~dp0requirements.txt"
start "" pythonw "%~dp0backup_studio.py"
