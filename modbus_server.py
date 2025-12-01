#!/usr/bin/env python3
"""Pymodbus Server With Callbacks.

This is an example of adding callbacks to a running modbus server
when a value is written to it.
"""
import asyncio
import logging.config
from concurrent.futures import ThreadPoolExecutor

import config

from pymodbus import ModbusDeviceIdentification, FramerType
from pymodbus.constants import ExcCodes
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext
)
from pymodbus.server import StartAsyncTcpServer

from ocr import usb_cams


OP_ADDRESS  = 1
OP_READY    = 0
OP_CAMERA   = 1
VALID_OPS = [OP_READY,OP_CAMERA]

STATUS_ADDRESS  = 2

STATUS_COMPLETE = 0
STATUS_WORKING  = 1
STATUS_ERROR    = 10

RESULT_ADDRESS  = 3

# Load the configuration file
start_config = config.read_config()
logging.config.dictConfig(start_config['logging'])

logger = logging.getLogger(__name__)

background_executor = ThreadPoolExecutor(max_workers=1)

class CallbackDataBlock(ModbusSequentialDataBlock):
    """A datablock that stores the new value in memory,
    and passes the operatio to a background thread executor for further processing.
    """

    logger = logging.getLogger(__name__)

    def __init__(self, addr, values):
        """Initialize."""
        super().__init__(addr, values)

    def setValues(self, address, value):
        """Set the requested values of the datastore."""
        logger.debug(f"Callback from setValues with address {address}, value {value}")

        # send async operation
        if address == OP_ADDRESS:
            op_code = value[0]
            prev_value = self.getValues(address)[0]
            if prev_value != op_code:
                return self.handle_operation(address, op_code)
            return ExcCodes.ACKNOWLEDGE
        else:
            return super().setValues(address, value)

    def handle_operation(self, address, op_code):
        if address is not OP_ADDRESS or op_code not in VALID_OPS:
            logger.error(f"Illegal operation value. Expecting one of {VALID_OPS}, got: {op_code}")
            return ExcCodes.ILLEGAL_VALUE
        if self.getValues(STATUS_ADDRESS, 1)[0] == STATUS_WORKING and op_code != OP_READY:
            logger.error(f"Server busy with previous action.")
            return ExcCodes.DEVICE_BUSY
        background_executor.submit(handle_background_task, op_code, self)
        return super().setValues(address, [op_code])

    def getValues(self, address, count=1):
        """Return the requested values from the datastore."""
        values = super().getValues(address, count=count)
        logger.debug(f"Callback from getValues with address {address}, count {count}, data {values}")
        return values

def handle_background_task(opcode: int, store):
    logger.debug(f"Executor got opcode: {opcode}")
    # if opcode == OP_READY:
        # do nothing
    if opcode == OP_CAMERA:
        store.setValues(STATUS_ADDRESS, [STATUS_WORKING]) # Working status
        logger.debug("Working status set")
        try:
            usb_cams.capture_all_cams(config=(config.read_config()))
            logger.debug("Executor completed")
            result = "OK"
            ints_list = list(result.encode("ascii"))
            ints_list.append(0) # terminate the string
            store.setValues(RESULT_ADDRESS, ints_list)
            store.setValues(STATUS_ADDRESS, [STATUS_COMPLETE]) # Complete status
        except Exception as e:
            store.setValues(STATUS_ADDRESS, [STATUS_ERROR])

async def run_callback_server(config):
    """Define datastore callback for server and do setup."""

    block = CallbackDataBlock(0x01, [0] * 1000)

    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0x01, [0] * 100),
        co=ModbusSequentialDataBlock(0x01, [0] * 100),
        hr=block,
        ir=ModbusSequentialDataBlock(0x01, [0] * 100)
    )
    context = ModbusServerContext(devices=store, single=True)

    # Device identity (optional)
    identity = ModbusDeviceIdentification()
    identity.VendorName = 'automato-ai'
    identity.ProductCode = 'M-OCR'
    identity.VendorUrl = 'https://github.com/automato-ai/maxima-ocr'
    identity.ProductName = 'Maxima OCR'
    identity.ModelName = 'ModbusServer'
    identity.MajorMinorRevision = '0.3'

    listenning_ip = config['modbus_server']['accept']
    listenning_port = config['modbus_server']['port']
    logger.info(f"Starting server on {listenning_ip}:{listenning_port}")
    await StartAsyncTcpServer(
        context=context,  # Data storage
        identity=identity,  # server identify
        address=(listenning_ip, listenning_port),  # listening address
        # custom_functions=[], # allow custom handling
        framer=FramerType.SOCKET,  # The framer strategy to use
        # ignore_missing_devices=True, # ignore request to a missing device
        # broadcast_enable=False, # treat device 0 as broadcast address,
        # timeout=1, # waiting time for request to complete
    )

    # graceful shutdown when server stopped
    background_executor.shutdown(False)


if __name__ == "__main__":
    asyncio.run(run_callback_server(start_config))

