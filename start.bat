@echo off
start "Coach Bot" /B .venv\Scripts\python.exe main.py > main.log 2>&1
start "Web Dashboard" /B .venv\Scripts\python.exe web.py > web.log 2>&1
echo Both services started. Logs: main.log / web.log
