from datetime import datetime

import cv2
import pytest

from ocr.usb_cams import capture_filename, get_cap_format


@pytest.mark.parametrize("name,expected", [
    ("ANY", cv2.CAP_ANY),
    ("DSHOW", cv2.CAP_DSHOW),
    ("QT", cv2.CAP_QT),
    ("MSMF", cv2.CAP_MSMF),
    ("OPENNI", cv2.CAP_OPENNI),
    ("FFMPEG", cv2.CAP_FFMPEG),
    ("OPENCV_MJPEG", cv2.CAP_OPENCV_MJPEG),
    ("V4L", cv2.CAP_V4L),
    ("V4L2", cv2.CAP_V4L2),
    ("INTEL_MFX", cv2.CAP_INTEL_MFX),
])
def test_get_cap_format_known_names(name, expected):
    assert get_cap_format(name) == expected


@pytest.mark.parametrize("name", ["", "AVFOUNDATION", "unknown", None, "any", "dshow"])
def test_get_cap_format_unknown_falls_back_to_any(name, caplog):
    assert get_cap_format(name) == cv2.CAP_ANY


def test_get_cap_format_unknown_logs_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="ocr.usb_cams"):
        get_cap_format("AVFOUNDATION")
    assert any("AVFOUNDATION" in r.message for r in caplog.records)


def test_capture_filename_format():
    t = datetime(2026, 5, 7, 14, 30, 45)
    assert capture_filename(t, 0) == "20260507-143045-0.mp4"


def test_capture_filename_pads_single_digit_time_components():
    t = datetime(2026, 1, 2, 3, 4, 5)
    assert capture_filename(t, 7) == "20260102-030405-7.mp4"


def test_capture_filename_includes_cam_id():
    t = datetime(2026, 5, 7, 14, 30, 45)
    assert capture_filename(t, 3).endswith("-3.mp4")
    assert capture_filename(t, 99).endswith("-99.mp4")
