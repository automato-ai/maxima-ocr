import asyncio
from unittest.mock import MagicMock, patch

import pytest
from pymodbus.constants import ExcCodes

import modbus_server
from modbus_server import (
    OP_ADDRESS,
    OP_CAMERA,
    OP_OCR,
    OP_READY,
    RESULT_ADDRESS,
    STATUS_ADDRESS,
    STATUS_COMPLETE,
    STATUS_ERROR,
    STATUS_WORKING,
    CallbackDataBlock,
    handle_background_task,
    run_callback_server,
)
from ocr.recognize import RecognitionResult


@pytest.fixture
def block():
    """Fresh datablock with the same shape `run_callback_server` builds."""
    return CallbackDataBlock(0x01, [0] * 1000)


@pytest.fixture
def submit_spy(monkeypatch):
    """Replace background_executor.submit with a spy so tests do not actually run OCR."""
    spy = MagicMock(name="background_executor.submit")
    monkeypatch.setattr(modbus_server.background_executor, "submit", spy)
    return spy


# ----- CallbackDataBlock.setValues: button semantics on OP_ADDRESS -----

class TestRisingEdgeTrigger:
    def test_writing_camera_op_from_ready_schedules_task(self, block, submit_spy):
        result = block.setValues(OP_ADDRESS, [OP_CAMERA])

        submit_spy.assert_called_once_with(
            modbus_server.handle_background_task, OP_CAMERA, block
        )
        assert block.getValues(OP_ADDRESS, 1) == [OP_CAMERA]
        # super().setValues returns None on success
        assert result is None

    def test_writing_same_op_again_returns_acknowledge_and_does_not_schedule(
        self, block, submit_spy
    ):
        block.setValues(OP_ADDRESS, [OP_CAMERA])
        submit_spy.reset_mock()

        result = block.setValues(OP_ADDRESS, [OP_CAMERA])

        assert result == ExcCodes.ACKNOWLEDGE
        submit_spy.assert_not_called()

    def test_release_button_writes_zero_and_schedules_no_op(self, block, submit_spy):
        # Simulate prior trigger so register holds 1.
        block.setValues(OP_ADDRESS, [OP_CAMERA])
        submit_spy.reset_mock()

        result = block.setValues(OP_ADDRESS, [OP_READY])

        # Release is allowed, gets stored, and the executor is invoked with OP_READY
        # (handle_background_task treats OP_READY as a no-op).
        assert block.getValues(OP_ADDRESS, 1) == [OP_READY]
        submit_spy.assert_called_once_with(
            modbus_server.handle_background_task, OP_READY, block
        )
        assert result is None


class TestIllegalValue:
    @pytest.mark.parametrize("bad_op", [3, 5, 99, 255])
    def test_invalid_op_returns_illegal_value_and_does_not_schedule(
        self, block, submit_spy, bad_op
    ):
        result = block.setValues(OP_ADDRESS, [bad_op])

        assert result == ExcCodes.ILLEGAL_VALUE
        submit_spy.assert_not_called()

    def test_invalid_op_does_not_overwrite_register(self, block, submit_spy):
        block.setValues(OP_ADDRESS, [OP_CAMERA])  # register now 1
        submit_spy.reset_mock()

        block.setValues(OP_ADDRESS, [42])

        assert block.getValues(OP_ADDRESS, 1) == [OP_CAMERA]


class TestDeviceBusy:
    def test_camera_op_while_working_returns_device_busy(self, block, submit_spy):
        # Mark the device as currently working.
        block.setValues(STATUS_ADDRESS, [STATUS_WORKING])

        result = block.setValues(OP_ADDRESS, [OP_CAMERA])

        assert result == ExcCodes.DEVICE_BUSY
        submit_spy.assert_not_called()

    def test_release_op_while_working_is_allowed(self, block, submit_spy):
        # Drive register 1 to OP_CAMERA without going through the trigger path
        # (so STATUS_WORKING is set but OP_ADDRESS reads 1 just like a real run).
        block.setValues(OP_ADDRESS, [OP_CAMERA])
        block.setValues(STATUS_ADDRESS, [STATUS_WORKING])
        submit_spy.reset_mock()

        result = block.setValues(OP_ADDRESS, [OP_READY])

        # OP_READY must always be accepted so the master can clear the button.
        assert result is None
        assert block.getValues(OP_ADDRESS, 1) == [OP_READY]


class TestOtherAddressesDelegate:
    def test_writes_to_non_op_addresses_pass_through(self, block, submit_spy):
        block.setValues(STATUS_ADDRESS, [STATUS_COMPLETE, STATUS_WORKING])

        assert block.getValues(STATUS_ADDRESS, 2) == [STATUS_COMPLETE, STATUS_WORKING]
        submit_spy.assert_not_called()

    def test_writes_to_result_block_pass_through(self, block, submit_spy):
        payload = [ord("H"), ord("I"), 0]
        block.setValues(RESULT_ADDRESS, payload)

        assert block.getValues(RESULT_ADDRESS, 3) == payload


# ----- handle_background_task: status/result transitions -----

class TestHandleBackgroundTask:
    def test_op_ready_is_a_no_op(self, block):
        # Pre-fill status/result with sentinels so we can prove they are untouched.
        block.setValues(STATUS_ADDRESS, [STATUS_COMPLETE])
        block.setValues(RESULT_ADDRESS, [ord("X"), 0])

        handle_background_task(OP_READY, block)

        assert block.getValues(STATUS_ADDRESS, 1) == [STATUS_COMPLETE]
        assert block.getValues(RESULT_ADDRESS, 2) == [ord("X"), 0]

    def test_camera_op_success_writes_result_and_complete_status(self, block):
        with patch.object(modbus_server.usb_cams, "capture_all_cams") as cap, \
             patch.object(modbus_server.config, "read_config", return_value={}):
            cap.return_value = None

            handle_background_task(OP_CAMERA, block)

        assert block.getValues(STATUS_ADDRESS, 1) == [STATUS_COMPLETE]
        # Result is ASCII bytes, one per register, terminated by 0.
        assert block.getValues(RESULT_ADDRESS, 3) == [ord("O"), ord("K"), 0]

    def test_camera_op_result_is_zero_terminated(self, block):
        """README protocol: master uses the trailing zero register to find the end."""
        with patch.object(modbus_server.usb_cams, "capture_all_cams"), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_CAMERA, block)

        # The register immediately after the ASCII payload must be 0.
        result_len = len("OK")
        assert block.getValues(RESULT_ADDRESS + result_len, 1) == [0]

    def test_camera_op_calls_capture_with_config(self, block):
        fake_config = {"camera": {"format": "ANY"}, "capture": {"folder": "./x", "frames": 1}}
        with patch.object(modbus_server.usb_cams, "capture_all_cams") as cap, \
             patch.object(modbus_server.config, "read_config", return_value=fake_config):
            handle_background_task(OP_CAMERA, block)

        cap.assert_called_once_with(config=fake_config)

    def test_camera_op_failure_sets_error_status(self, block):
        with patch.object(modbus_server.usb_cams, "capture_all_cams",
                          side_effect=RuntimeError("camera blew up")), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_CAMERA, block)

        assert block.getValues(STATUS_ADDRESS, 1) == [STATUS_ERROR]

    def test_camera_op_sets_working_status_before_capture(self, block):
        observed = {}

        def record_status_during_capture(*, config):
            observed["status"] = block.getValues(STATUS_ADDRESS, 1)[0]

        with patch.object(modbus_server.usb_cams, "capture_all_cams",
                          side_effect=record_status_during_capture), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_CAMERA, block)

        assert observed["status"] == STATUS_WORKING


# ----- OP_OCR end-to-end on the protocol surface -----

class TestOcrTriggerPath:
    def test_op_ocr_rising_edge_schedules_task(self, block, submit_spy):
        result = block.setValues(OP_ADDRESS, [OP_OCR])

        submit_spy.assert_called_once_with(
            modbus_server.handle_background_task, OP_OCR, block
        )
        assert block.getValues(OP_ADDRESS, 1) == [OP_OCR]
        assert result is None

    def test_op_ocr_repeat_returns_acknowledge(self, block, submit_spy):
        block.setValues(OP_ADDRESS, [OP_OCR])
        submit_spy.reset_mock()

        result = block.setValues(OP_ADDRESS, [OP_OCR])

        assert result == ExcCodes.ACKNOWLEDGE
        submit_spy.assert_not_called()

    def test_op_ocr_while_working_returns_busy(self, block, submit_spy):
        block.setValues(STATUS_ADDRESS, [STATUS_WORKING])

        result = block.setValues(OP_ADDRESS, [OP_OCR])

        assert result == ExcCodes.DEVICE_BUSY
        submit_spy.assert_not_called()


class TestHandleBackgroundTaskOcr:
    def test_ocr_op_writes_recognized_text_and_complete_status(self, block):
        with patch.object(modbus_server.recognize, "recognize_cylinder",
                          return_value=RecognitionResult(ok=True, text="ABC123")), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_OCR, block)

        assert block.getValues(STATUS_ADDRESS, 1) == [STATUS_COMPLETE]
        expected = [ord("A"), ord("B"), ord("C"), ord("1"), ord("2"), ord("3"), 0]
        assert block.getValues(RESULT_ADDRESS, len(expected)) == expected

    def test_ocr_op_unrecognized_writes_error_description_and_error_status(self, block):
        with patch.object(modbus_server.recognize, "recognize_cylinder",
                          return_value=RecognitionResult(ok=False, text="unrecognized")), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_OCR, block)

        assert block.getValues(STATUS_ADDRESS, 1) == [STATUS_ERROR]
        # The failure description is written to the result registers as ASCII bytes
        # so the Modbus master can read it from RESULT_ADDRESS.
        expected = list(b"unrecognized") + [0]
        assert block.getValues(RESULT_ADDRESS, len(expected)) == expected

    def test_ocr_op_cameras_not_found_writes_description_and_error_status(self, block):
        with patch.object(modbus_server.recognize, "recognize_cylinder",
                          return_value=RecognitionResult(ok=False, text="cameras not found")), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_OCR, block)

        assert block.getValues(STATUS_ADDRESS, 1) == [STATUS_ERROR]
        expected = list(b"cameras not found") + [0]
        assert block.getValues(RESULT_ADDRESS, len(expected)) == expected

    def test_ocr_op_exception_sets_error_status(self, block):
        with patch.object(modbus_server.recognize, "recognize_cylinder",
                          side_effect=RuntimeError("bundle missing")), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_OCR, block)

        assert block.getValues(STATUS_ADDRESS, 1) == [STATUS_ERROR]

    def test_ocr_op_sets_working_status_before_recognize(self, block):
        observed = {}

        def record_status(_cfg):
            observed["status"] = block.getValues(STATUS_ADDRESS, 1)[0]
            return RecognitionResult(ok=True, text="X")

        with patch.object(modbus_server.recognize, "recognize_cylinder",
                          side_effect=record_status), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_OCR, block)

        assert observed["status"] == STATUS_WORKING

    def test_ocr_op_does_not_invoke_video_capture(self, block):
        with patch.object(modbus_server.recognize, "recognize_cylinder",
                          return_value=RecognitionResult(ok=True, text="X")), \
             patch.object(modbus_server.usb_cams, "capture_all_cams") as cap, \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_OCR, block)

        cap.assert_not_called()

    def test_ocr_op_result_is_zero_terminated(self, block):
        with patch.object(modbus_server.recognize, "recognize_cylinder",
                          return_value=RecognitionResult(ok=True, text="42")), \
             patch.object(modbus_server.config, "read_config", return_value={}):
            handle_background_task(OP_OCR, block)

        # Register immediately after the ASCII payload must be 0.
        assert block.getValues(RESULT_ADDRESS + len("42"), 1) == [0]


# ----- startup: eager pipeline load -----

class TestStartupPipelineLoad:
    def _run(self, coro):
        try:
            asyncio.run(coro)
        except SystemExit as e:
            return e

    def test_startup_loads_pipeline_eagerly(self):
        cfg = {"modbus_server": {"accept": "127.0.0.1", "port": 5020}}

        async def never_returns(**_kwargs):
            # Stop the server coroutine before it actually binds a socket;
            # we only care that load_pipeline ran before this point.
            raise asyncio.CancelledError()

        with patch.object(modbus_server.recognize, "load_pipeline") as load, \
             patch.object(modbus_server, "StartAsyncTcpServer", side_effect=never_returns):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(run_callback_server(cfg))

        load.assert_called_once_with(cfg)

    def test_startup_exits_when_pipeline_load_fails(self):
        cfg = {"modbus_server": {"accept": "127.0.0.1", "port": 5020}}

        with patch.object(modbus_server.recognize, "load_pipeline",
                          side_effect=RuntimeError("Bundle validation failed: md5 mismatch")), \
             patch.object(modbus_server, "StartAsyncTcpServer") as srv:
            with pytest.raises(SystemExit) as exc:
                asyncio.run(run_callback_server(cfg))

        assert exc.value.code != 0
        srv.assert_not_called()
