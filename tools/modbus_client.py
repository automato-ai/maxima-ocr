#!/usr/bin/env python3
"""Dev/test Modbus client for the maxima-ocr server.

================================================================================
  NOT FOR PRODUCTION USE — development & integration testing only.

  This script is intentionally placed under tools/ so it is excluded from the
  shipped Windows distributable (compile.bat only bundles modbus_server.py).

  Purpose: drive OP_CAMERA / OP_OCR triggers and inspect status / result
  registers without a PLC master attached. Useful when bringing up a new
  factory PC, debugging the OCR pipeline, or smoke-testing a build.
================================================================================

Examples (run from the repo root):

    python tools/modbus_client.py ocr
    python tools/modbus_client.py camera --host 127.0.0.1 --port 502
    python tools/modbus_client.py status
    python tools/modbus_client.py result

Wire-address note:
    pymodbus 3.x's slave context offsets incoming addresses by +1 before
    invoking the server's data block (see pymodbus/datastore/context.py).
    The deployed server therefore treats wire register N as its internal
    address N+1. To hit the server's OP_ADDRESS=1 / STATUS_ADDRESS=2 /
    RESULT_ADDRESS=3, this client must write/read wire registers 0 / 1 / 2.
    The factory PLC integration is already wired this way; do not change the
    server-side constants — change them here instead.
"""
import argparse
import sys
import time

from pymodbus.client import ModbusTcpClient


# Wire addresses (1 less than the server-side constants in modbus_server.py).
OP_ADDRESS = 0      # server OP_ADDRESS=1
STATUS_ADDRESS = 1  # server STATUS_ADDRESS=2
RESULT_ADDRESS = 2  # server RESULT_ADDRESS=3

OP_READY = 0
OP_CAMERA = 1
OP_OCR = 2

STATUS_COMPLETE = 0
STATUS_WORKING = 1
STATUS_ERROR = 10
STATUS_NAMES = {STATUS_COMPLETE: "COMPLETE", STATUS_WORKING: "WORKING", STATUS_ERROR: "ERROR"}

MAX_RESULT_REGISTERS = 64

POLL_INTERVAL_SEC = 0.2
DEFAULT_TIMEOUT_SEC = 60.0

# Modbus exception code 5 (ACKNOWLEDGE). The server returns this when a
# write to the OP register has the same value as is already stored (see
# modbus_server.CallbackDataBlock.setValues — "no-op write" path). pymodbus
# surfaces it as an error response, but it's not a failure for our purposes.
MODBUS_EXC_ACKNOWLEDGE = 5


def _check(rsp, what):
    if rsp.isError():
        raise RuntimeError(f"{what} failed: {rsp}")
    return rsp


def _is_acknowledge(rsp):
    return getattr(rsp, "exception_code", None) == MODBUS_EXC_ACKNOWLEDGE


def read_status(client):
    rsp = _check(client.read_holding_registers(STATUS_ADDRESS, count=1), "read status")
    return rsp.registers[0]


def read_result(client):
    rsp = _check(
        client.read_holding_registers(RESULT_ADDRESS, count=MAX_RESULT_REGISTERS),
        "read result",
    )
    out = bytearray()
    for v in rsp.registers:
        if v == 0:
            break
        out.append(v & 0xFF)
    return out.decode("ascii", errors="replace")


def write_op(client, op_code):
    rsp = client.write_register(OP_ADDRESS, op_code)
    if rsp.isError() and not _is_acknowledge(rsp):
        raise RuntimeError(f"write op {op_code} failed: {rsp}")


def wait_until_idle(client, timeout):
    """Block until STATUS is not WORKING. Returns the terminal status code."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = read_status(client)
        if s != STATUS_WORKING:
            return s
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"timed out after {timeout}s waiting for completion")


def connect(args):
    client = ModbusTcpClient(host=args.host, port=args.port, timeout=args.timeout)
    if not client.connect():
        raise ConnectionError(f"cannot connect to {args.host}:{args.port}")
    return client


def cmd_trigger(args, op_code, op_name):
    with connect(args) as client:
        # Guarantee a rising edge: clear to READY first, waiting out any in-flight op.
        if read_status(client) == STATUS_WORKING:
            print("server busy; waiting for current op to finish...")
            wait_until_idle(client, args.timeout)
        write_op(client, OP_READY)

        print(f"trigger {op_name} (op={op_code}) -> {args.host}:{args.port}")
        write_op(client, op_code)
        try:
            status = wait_until_idle(client, args.timeout)
        finally:
            # Release the "button" so the next trigger is also a rising edge.
            write_op(client, OP_READY)

        name = STATUS_NAMES.get(status, f"UNKNOWN({status})")
        if status == STATUS_COMPLETE:
            text = read_result(client)
            print(f"status: {name}")
            print(f"result: {text!r}")
            return 0
        print(f"status: {name}", file=sys.stderr)
        return 1


def cmd_status(args):
    with connect(args) as client:
        s = read_status(client)
        print(f"status: {STATUS_NAMES.get(s, 'UNKNOWN')} ({s})")
        return 0


def cmd_result(args):
    with connect(args) as client:
        print(read_result(client))
        return 0


def cmd_release(args):
    """Write OP_READY without triggering anything. Useful to recover a stuck state."""
    with connect(args) as client:
        write_op(client, OP_READY)
        print("released (OP_READY written)")
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SEC,
        help="seconds to wait for a triggered op to complete (also the socket timeout)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("camera",  help="trigger OP_CAMERA, poll status, print result")
    sub.add_parser("ocr",     help="trigger OP_OCR, poll status, print recognized text")
    sub.add_parser("status",  help="read STATUS_ADDRESS once")
    sub.add_parser("result",  help="read RESULT_ADDRESS once (ASCII until 0)")
    sub.add_parser("release", help="write OP_READY (clear the trigger button)")

    args = parser.parse_args()
    handlers = {
        "camera":  lambda: cmd_trigger(args, OP_CAMERA, "camera"),
        "ocr":     lambda: cmd_trigger(args, OP_OCR, "ocr"),
        "status":  lambda: cmd_status(args),
        "result":  lambda: cmd_result(args),
        "release": lambda: cmd_release(args),
    }
    try:
        return handlers[args.cmd]()
    except (ConnectionError, RuntimeError, TimeoutError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
