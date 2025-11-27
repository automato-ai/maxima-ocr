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

The protocol on top of modbus id as follows:

1. Master triggers operation by writing value `1` to the holding register at address `1`
2. The camera operation starts. This operation can take a few seconds. The execution status can be monitored on 
   holding register `2`. The monitoring is optional. See the [status codes](#status-codes-for-monitoring) below.
3. Once the operation is complete, the value at register `1` will become `0`, indicating that no operation is 
   currently performed and the slave is ready for next operation.
4. The result of the last executed operation can be read from the holding registers starting at address `3`.

#### Status codes for monitoring
Optionally, the master can monitor the execution of the operation by reading the execution status from 
holding register at address `2` during the operation. The possible values are:

| Value of  holding register at address `2` | Execution status    |
|-------------------------------------------|---------------------|
| `0`                                       | Complete            |
| `1`                                       | Working             |
| `10`                                      | Complete with error |
