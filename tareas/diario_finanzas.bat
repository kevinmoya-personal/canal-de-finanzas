@echo off
rem Produce y programa el contenido de MAÑANA de Plata con Datos.
rem Lo lanza el Programador de tareas de Windows cada noche.
cd /d D:\Proyectos\video-studio
set PYTHONIOENCODING=utf-8
echo. >> logs\diario_finanzas.log
echo ===== %date% %time% ===== >> logs\diario_finanzas.log
python cli.py diario finanzas >> logs\diario_finanzas.log 2>&1
