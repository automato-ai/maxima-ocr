"""Headless tests for tools/replay.py.

The interactive cv2.imshow loop is not exercised here — these tests cover the
composition path that turns a recorded session into a single BGR canvas, plus
the auto-discovery of the latest meta.json in a folder.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
REPLAY_PATH = REPO_ROOT / "tools" / "replay.py"


def _load_replay():
    spec = importlib.util.spec_from_file_location("replay_tool", REPLAY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = _load_replay()


def _write_meta(folder: Path, prefix: str, frames: list, cameras=(0,),
                readiness_enabled=True, outcome=None) -> Path:
    meta = {
        "session": prefix,
        "cameras": list(cameras),
        "fps": 25,
        "readiness_enabled": readiness_enabled,
        "frames": frames,
        "outcome": outcome or {"ok": True, "text": "OK"},
    }
    path = folder / f"{prefix}-meta.json"
    path.write_text(json.dumps(meta))
    return path


def _write_mp4(folder: Path, prefix: str, cam: int, n_frames: int = 3,
               w: int = 80, h: int = 60) -> Path:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    path = folder / f"{prefix}-{cam}.mp4"
    writer = cv2.VideoWriter(str(path), fourcc, 25, (w, h))
    if not writer.isOpened():
        pytest.skip("mp4v codec unavailable in this cv2 build")
    for i in range(n_frames):
        frame = np.full((h, w, 3), 50 + i * 30, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path


def _crop_path(folder: Path, prefix: str, frame_idx: int, cam: int, kind: str,
               w: int = 30, h: int = 12) -> str:
    crops = folder / f"{prefix}-crops"
    crops.mkdir(exist_ok=True)
    name = f"{frame_idx:04d}-{cam}-{kind}.png"
    img = np.full((h, w, 3), 200, dtype=np.uint8) if kind == "orig" \
        else np.full((h, w), 128, dtype=np.uint8)
    cv2.imwrite(str(crops / name), img)
    return f"{prefix}-crops/{name}"


class TestResolveMetaPath:
    def test_returns_file_path_unchanged(self, tmp_path):
        meta = _write_meta(tmp_path, "20260511-153012", frames=[])
        assert replay.resolve_meta_path(meta) == meta

    def test_picks_latest_meta_when_given_folder(self, tmp_path):
        first = _write_meta(tmp_path, "20260511-120000", frames=[])
        time.sleep(0.05)  # filesystem mtime resolution on Windows
        second = _write_meta(tmp_path, "20260511-153012", frames=[])
        # Mtime of `second` is newest → that's the one we want.
        assert replay.resolve_meta_path(tmp_path) == second

    def test_errors_when_folder_has_no_meta(self, tmp_path):
        with pytest.raises(SystemExit):
            replay.resolve_meta_path(tmp_path)

    def test_errors_when_path_does_not_exist(self, tmp_path):
        with pytest.raises(SystemExit):
            replay.resolve_meta_path(tmp_path / "missing")


class TestSession:
    def test_loads_metadata_and_opens_each_camera_video(self, tmp_path):
        prefix = "20260511-153012"
        _write_meta(tmp_path, prefix, frames=[], cameras=(0, 1))
        _write_mp4(tmp_path, prefix, cam=0)
        _write_mp4(tmp_path, prefix, cam=1)

        sess = replay.Session(tmp_path / f"{prefix}-meta.json")
        try:
            assert sess.cameras == [0, 1]
            assert len(sess.captures) == 2
            for cap in sess.captures:
                assert cap.isOpened()
        finally:
            sess.close()

    def test_raises_when_video_missing(self, tmp_path):
        prefix = "20260511-153012"
        _write_meta(tmp_path, prefix, frames=[], cameras=(0,))
        # No mp4 written.
        with pytest.raises(RuntimeError):
            replay.Session(tmp_path / f"{prefix}-meta.json")


class TestCompose:
    def _session_with_one_camera(self, tmp_path, cam_meta_overrides=None):
        prefix = "20260511-153012"
        frame_meta = {
            "frame": 0,
            "cameras": [
                {
                    "cam": 0,
                    "readiness": {"ready": True, "confidence": 0.92},
                    "bbox": {"bbox": [10, 10, 50, 30], "confidence": 0.8,
                             "rotation": 0.0},
                    "ocr": {
                        "text": "5", "confidence": 0.7,
                        "digits": [
                            {"digit": 5, "x1": 2, "y1": 2, "x2": 12, "y2": 10,
                             "confidence": 0.7}
                        ],
                        "crop": _crop_path(tmp_path, prefix, 0, 0, "orig"),
                        "preproc": _crop_path(tmp_path, prefix, 0, 0, "prep"),
                    },
                    "aggregation": {
                        "text": "5", "confidence": 0.7, "status": "accumulating",
                        "frames_processed": 1, "reason": None,
                        "expected_digits": 1,
                        "candidates": [
                            {"digit": 5, "confidence": 0.7,
                             "frame_x": 30, "frame_y": 20, "crop_x": 12,
                             "vote_count": 1, "frame_count": 1,
                             "consistency": 1.0, "selected": True},
                        ],
                    },
                },
            ],
            "agg_status": "accumulating",
            "fused": None,
        }
        if cam_meta_overrides is not None:
            frame_meta["cameras"][0].update(cam_meta_overrides)
        _write_meta(tmp_path, prefix, frames=[frame_meta], cameras=(0,))
        _write_mp4(tmp_path, prefix, cam=0, n_frames=2)
        return replay.Session(tmp_path / f"{prefix}-meta.json")

    def test_compose_layout_produces_a_bgr_image(self, tmp_path):
        sess = self._session_with_one_camera(tmp_path)
        try:
            frames = sess.read_frame(0)
            out = replay.compose_layout(sess, 0, frames, paused=False, speed=1.0)
            assert out.ndim == 3
            assert out.shape[2] == 3
            assert out.dtype == np.uint8
            # Header on top + a column of (video + 2 crops + 2 gutters).
            expected_h = (
                replay.HEADER_H + replay.VIDEO_PANEL_H + 2 * replay.CROP_PANEL_H
                + 2 * replay.GUTTER
            )
            assert out.shape[0] == expected_h
        finally:
            sess.close()

    def test_compose_layout_handles_missing_bbox_and_ocr(self, tmp_path):
        # An early frame: readiness only, no bbox/ocr/aggregation yet.
        sess = self._session_with_one_camera(tmp_path, cam_meta_overrides={
            "bbox": None, "ocr": None, "aggregation": None,
        })
        try:
            frames = sess.read_frame(0)
            out = replay.compose_layout(sess, 0, frames, paused=True, speed=1.0)
            # Must render without exceptions even when there are no overlays.
            assert out.ndim == 3
        finally:
            sess.close()

    def test_compose_layout_marks_readiness_disabled(self, tmp_path):
        prefix = "20260511-153012"
        frame_meta = {
            "frame": 0,
            "cameras": [{"cam": 0, "readiness": {"disabled": True},
                         "bbox": None, "ocr": None, "aggregation": None}],
            "agg_status": None, "fused": None,
        }
        _write_meta(tmp_path, prefix, frames=[frame_meta],
                    readiness_enabled=False, cameras=(0,))
        _write_mp4(tmp_path, prefix, cam=0, n_frames=1)

        sess = replay.Session(tmp_path / f"{prefix}-meta.json")
        try:
            frames = sess.read_frame(0)
            out = replay.compose_layout(sess, 0, frames, paused=True, speed=1.0)
            assert out.ndim == 3
        finally:
            sess.close()


class TestKeyActions:
    @pytest.mark.parametrize("key,expected", [
        (ord('q'), "quit"),
        (27, "quit"),  # ESC
        (ord(' '), "toggle_pause"),
        (ord(']'), "faster"),
        (ord('['), "slower"),
        (83, "step_forward"),
        (81, "step_back"),
        (0x27, "step_forward"),
        (0x25, "step_back"),
    ])
    def test_recognized_keys(self, key, expected):
        assert replay._key_action(key) == expected

    def test_unrecognized_key_returns_none(self):
        assert replay._key_action(ord('z')) is None
        assert replay._key_action(0xFFFF) is None
