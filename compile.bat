pyinstaller --onefile modbus_server.py
copy ".\config.yaml" ".\dist\" /y
pause