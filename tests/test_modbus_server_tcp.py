"""TCP-level integration tests for the Modbus server.

These cover the path that direct-callback unit tests cannot: pymodbus
deserialising a wire request and dispatching it into our `CallbackDataBlock`.

Wire-address note: pymodbus 3.x's slave context adds +1 to every wire address
before invoking the data block, so a master that wants to hit the server's
OP_ADDRESS=1 / STATUS_ADDRESS=2 / RESULT_ADDRESS=3 must write/read wire
registers 0 / 1 / 2. The factory PLC integration is already wired this way.
"""
import asyncio
import socket
import threading
import time

import pytest
from pymodbus.client import ModbusTcpClient

import modbus_server
from modbus_server import (
    OP_OCR,
    OP_READY,
    STATUS_COMPLETE,
    STATUS_WORKING,
)
from ocr.recognize import RecognitionResult

# Wire addresses (one less than the corresponding server-side constants).
WIRE_OP_REGISTER     = modbus_server.OP_ADDRESS - 1      # 0
WIRE_STATUS_REGISTER = modbus_server.STATUS_ADDRESS - 1  # 1
WIRE_RESULT_REGISTER = modbus_server.RESULT_ADDRESS - 1  # 2


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host, port, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"server never started on {host}:{port}")


@pytest.fixture
def running_server(monkeypatch):
    """Start `run_callback_server` on a free port in a background thread.

    OCR/camera dependencies are stubbed so we exercise only the wire path.
    """
    monkeypatch.setattr(modbus_server.recognize, "load_pipeline", lambda cfg: None)
    monkeypatch.setattr(
        modbus_server.recognize,
        "recognize_cylinder",
        lambda cfg: RecognitionResult(ok=True, text="WIRE-OK"),
    )
    monkeypatch.setattr(modbus_server.config, "read_config", lambda: {})

    host, port = "127.0.0.1", _free_port()
    cfg = {"modbus_server": {"accept": host, "port": port}}

    loop = asyncio.new_event_loop()
    holder = {}

    def run():
        asyncio.set_event_loop(loop)
        holder["task"] = loop.create_task(modbus_server.run_callback_server(cfg))
        try:
            loop.run_forever()
        finally:
            loop.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    _wait_for_port(host, port)

    yield host, port

    loop.call_soon_threadsafe(holder["task"].cancel)
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)


def _poll_status(client, want_not=STATUS_WORKING, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rr = client.read_holding_registers(WIRE_STATUS_REGISTER, count=1)
        assert not rr.isError(), rr
        if rr.registers[0] != want_not:
            return rr.registers[0]
        time.sleep(0.05)
    pytest.fail(f"status stuck at {want_not} after {timeout}s")


class TestOcrTriggerOverTcp:
    def test_ocr_trigger_runs_recognize_and_writes_result(self, running_server):
        host, port = running_server
        with ModbusTcpClient(host, port=port, timeout=5) as client:
            assert client.connect()

            # Clear to READY then trigger OCR.
            client.write_register(WIRE_OP_REGISTER, OP_READY)
            client.write_register(WIRE_OP_REGISTER, OP_OCR)

            status = _poll_status(client)
            assert status == STATUS_COMPLETE

            rr = client.read_holding_registers(WIRE_RESULT_REGISTER, count=16)
            assert not rr.isError(), rr
            payload = bytes(v & 0xFF for v in rr.registers).split(b"\x00", 1)[0]
            assert payload == b"WIRE-OK"

    def test_repeat_same_op_does_not_retrigger(self, running_server, monkeypatch):
        """Re-writing the same op while register already holds it must be a no-op
        (the trigger is rising-edge). Regression for the case where address
        offset on the wire made every write a 'first' write."""
        host, port = running_server
        calls = []
        monkeypatch.setattr(
            modbus_server.recognize,
            "recognize_cylinder",
            lambda cfg: (calls.append(1), RecognitionResult(ok=True, text="X"))[1],
        )

        with ModbusTcpClient(host, port=port, timeout=5) as client:
            client.write_register(WIRE_OP_REGISTER, OP_READY)
            client.write_register(WIRE_OP_REGISTER, OP_OCR)
            _poll_status(client)

            # Second write of OP_OCR without a release in between must not
            # schedule another run.
            client.write_register(WIRE_OP_REGISTER, OP_OCR)
            time.sleep(0.2)

        assert len(calls) == 1
