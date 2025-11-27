#!/usr/bin/env python3
"""Pymodbus Server With Callbacks.

This is an example of adding callbacks to a running modbus server
when a value is written to it.
"""
import asyncio
import logging
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


OP_ADDRESS = 1
VALID_OPS = [
    0, # No operation = Ready
    1  # Read ID from camera
]

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class CallbackDataBlock(ModbusSequentialDataBlock):
    """A datablock that stores the new value in memory,
    and passes the operation to a message queue for further processing.
    """

    def __init__(self, queue, addr, values):
        """Initialize."""
        self.queue = queue
        super().__init__(addr, values)

    def setValues(self, address, value):
        """Set the requested values of the datastore."""
        logger.debug(f"Callback from setValues with address {address}, value {value}")

        # send async operation
        if address == OP_ADDRESS:
            return self.handle_operation(address, value)
        else:
            return super().setValues(address, value)

    def handle_operation(self, address, value):
        op_code = value[0]
        if address is not OP_ADDRESS or op_code not in VALID_OPS:
            logger.error(f"Illegal operation value. Expecting one of {VALID_OPS}, got: {op_code}")
            return ExcCodes.ILLEGAL_VALUE
        asyncio.create_task(
            self.queue.put({
                'op_code': value
            })
        )
        return super().setValues(address, value)


    def getValues(self, address, count=1):
        """Return the requested values from the datastore."""
        values = super().getValues(address, count=count)
        logger.debug(f"Callback from getValues with address {address}, count {count}, data {values}")
        return values

async def background_executor(q, store):
    while True:
        try:
            data = await q.get()
            print(f"Executor got: {data}")
            opcode = data['op_code'][0]
            if opcode == 1: # Read from camera
                store.setValues(2, [1]) # Working status
                print("Working status set")
                usb_cams.capture_all_cams(config=(config.read_config()))
                # await asyncio.sleep(10) # Simulate processing
                print("Executor completed")
                store.setValues(2, [0]) # Complete status
                store.setValues(3, "OK".encode("ascii"))
                store.setValues(1, [0]) # Ready for the next operation
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
        finally:
            q.task_done()

async def run_callback_server():
    conf = config.read_config()
    """Define datastore callback for server and do setup."""
    queue: asyncio.Queue = asyncio.Queue()

    block = CallbackDataBlock(queue, 0x01, [0] * 1000)
    background_task = asyncio.create_task(background_executor(queue, block))

    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0x01, [0] * 100),
        co=ModbusSequentialDataBlock(0x01, [0] * 100),
        hr=block,
        ir=ModbusSequentialDataBlock(0x01, [0] * 100)
    )
    context = ModbusServerContext(devices=store, single=True)

    # Device identity (optional)
    identity = ModbusDeviceIdentification()
    identity.VendorName = 'ExampleCorp'
    identity.ProductCode = 'EM'
    identity.VendorUrl = 'http://example.com'
    identity.ProductName = 'Modbus Random Data Server'
    identity.ModelName = 'ModbusServer'
    identity.MajorMinorRevision = '1.0'

    await StartAsyncTcpServer(
        context=context,  # Data storage
        identity=identity,  # server identify
        address=(conf['modbus_server']['accept'], conf['modbus_server']['port']),  # listening address
        # custom_functions=[], # allow custom handling
        framer=FramerType.SOCKET,  # The framer strategy to use
        # ignore_missing_devices=True, # ignore request to a missing device
        # broadcast_enable=False, # treat device 0 as broadcast address,
        # timeout=1, # waiting time for request to complete
    )

    # graceful shutdown when server stopped
    background_task.cancel()
    try:
        await background_task
    except asyncio.CancelledError:
        print("Consumer task cancelled.")


if __name__ == "__main__":
    FORMAT = ('%(asctime)-15s %(threadName)-15s %(levelname)-8s %(module)-15s:'
              '%(lineno)-8s %(message)s')
    logging.basicConfig(format=FORMAT)
    logging.getLogger().setLevel(logging.INFO)

    asyncio.run(run_callback_server())

