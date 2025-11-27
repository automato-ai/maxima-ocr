# maxima-ocr
A custom OCR python program for Maxima

## Quick Start

### Download and execute
1. Download maxima-ocr-xx.zip here: https://github.com/automato-ai/maxima-ocr/releases
2. Extract on your Windows machine.
3. Execute the `modbus_server.exe` file.

### Configuration
The runtime can be configured by changing the `config.yaml` file and restarting the service.

### Integration via modbus TCP
This program is a modbus TCP server (modbus slave) and can be controlled via TCP modbus communication.
The default listening port is `502`, but can be changed in the `config.yaml` file.

To trigger the camera operation, the master is expected to write the operation code to holding register 
at address `1`. Currently, the supported codes to be writen to address `1` are:
- `1` - perform camera operation. (Record short video from each connected camera)
- `0` - noop. Ready for command.

Optionally, the master can monitor the execution of the operation by reading the execution status from 
holding register at address `2`. The possible values are:
- `1` - Working
- `0` - Complete
- `2` - Error

Once the operation is complete, the result can be read from the holding registers starting at address `3`.
