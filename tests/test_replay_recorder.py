"""Tests for ocr.replay_recorder.

The recorder owns one OCR-session artifact set: one MP4 per camera (named via
ocr.usb_cams.capture_filename) plus a {prefix}-meta.json with per-tick model
decisions. cv2.VideoWriter is mocked here so the tests don't depend on the
host having a working mp4v codec — the assertion is that the recorder *asked*
for a writer per camera, with the right filename and frame shape, and wrote
each frame exactly once.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ocr import replay_recorder


def _frame(w=64, h=48):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _readiness(ready=True, conf=0.9):
    return SimpleNamespace(ready=ready, confidence=conf)


def _bbox(coords=(0, 0, 10, 10), conf=0.9, rot=0.0):
    return SimpleNamespace(bbox=coords, confidence=conf, rotation=rot)


def _digit(d=5, x1=1, y1=2, x2=3, y2=4, conf=0.7):
    return SimpleNamespace(digit=d, x1=x1, y1=y1, x2=x2, y2=y2, confidence=conf)


def _ocr(text="123", conf=0.85, digits=None):
    return SimpleNamespace(text=text, confidence=conf, digit_detections=digits)


def _agg(text="12", conf=0.7, status="accumulating", frames=5, reason=None):
    return SimpleNamespace(
        text=text, confidence=conf, status=status,
        frames_processed=frames, reason=reason,
    )


def _result(readiness=None, bboxes=None, ocr=None, aggregation=None, fused=None):
    return SimpleNamespace(
        readiness=readiness or [],
        bboxes=bboxes,
        ocr=ocr,
        aggregation=aggregation,
        fused_result=fused,
    )


def _outcome(ok=True, text="OK"):
    return SimpleNamespace(ok=ok, text=text)


@pytest.fixture
def fake_writer():
    """Replace cv2.VideoWriter with a MagicMock so tests are codec-independent."""
    with patch.object(replay_recorder.cv2, "VideoWriter") as ctor:
        ctor.return_value = MagicMock()
        yield ctor


def _read_meta(folder: Path) -> dict:
    files = list(folder.glob("*-meta.json"))
    assert len(files) == 1, f"expected exactly one meta.json, got {files}"
    return json.loads(files[0].read_text())


class TestSessionArtifacts:
    def test_opens_one_video_writer_per_camera_with_capture_filename(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0, 3], readiness_enabled=True,
        )
        rec.record_tick([_frame(), _frame()],
                        _result(readiness=[_readiness(), _readiness()]))
        rec.finalize(_outcome())

        assert fake_writer.call_count == 2
        # Each call: (path, fourcc, fps, (w, h)).
        paths = [Path(call.args[0]).name for call in fake_writer.call_args_list]
        assert all(re.fullmatch(r"\d{8}-\d{6}-(0|3)\.mp4", n) for n in paths)
        # Frame size derived from actual frame shape.
        for call in fake_writer.call_args_list:
            assert call.args[3] == (64, 48)

    def test_writes_each_tick_frame_to_its_camera_writer(self, tmp_path, fake_writer):
        writers = [MagicMock(), MagicMock()]
        fake_writer.side_effect = writers
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0, 1], readiness_enabled=True,
        )
        f0a, f1a = _frame(), _frame()
        f0b, f1b = _frame(), _frame()
        rec.record_tick([f0a, f1a], _result(readiness=[_readiness(), _readiness()]))
        rec.record_tick([f0b, f1b], _result(readiness=[_readiness(), _readiness()]))
        rec.finalize(_outcome())

        # cam 0 writer got f0a then f0b; cam 1 writer got f1a then f1b.
        assert [c.args[0] is f0a for c in writers[0].write.call_args_list] == [True, False]
        assert writers[0].write.call_count == 2
        assert writers[1].write.call_count == 2

    def test_writers_released_on_finalize(self, tmp_path, fake_writer):
        writer = MagicMock()
        fake_writer.return_value = writer
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick([_frame()], _result(readiness=[_readiness()]))
        rec.finalize(_outcome())

        writer.release.assert_called_once()

    def test_meta_json_filename_shares_prefix_with_videos(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick([_frame()], _result(readiness=[_readiness()]))
        rec.finalize(_outcome())

        meta_path = next(tmp_path.glob("*-meta.json"))
        video_filename = Path(fake_writer.call_args_list[0].args[0]).name
        # MP4 name is "{prefix}-{cam}.mp4"; meta is "{prefix}-meta.json".
        video_prefix = video_filename.rsplit("-", 1)[0]
        assert meta_path.name == f"{video_prefix}-meta.json"

    def test_finalize_without_any_ticks_still_writes_meta(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.finalize(_outcome(ok=False, text="cameras not ready"))

        meta = _read_meta(tmp_path)
        assert meta["frames"] == []
        assert meta["outcome"] == {"ok": False, "text": "cameras not ready"}
        # No writers were ever opened, so no mp4s exist.
        fake_writer.assert_not_called()


class TestMetadataHeader:
    def test_header_records_cameras_fps_and_session(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0, 1, 2], readiness_enabled=True,
        )
        rec.finalize(_outcome())

        meta = _read_meta(tmp_path)
        assert meta["cameras"] == [0, 1, 2]
        assert meta["fps"] == 25
        assert re.fullmatch(r"\d{8}-\d{6}", meta["session"])

    def test_header_propagates_readiness_enabled_true(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.finalize(_outcome())

        assert _read_meta(tmp_path)["readiness_enabled"] is True

    def test_header_propagates_readiness_enabled_false(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=False,
        )
        rec.finalize(_outcome())

        assert _read_meta(tmp_path)["readiness_enabled"] is False


class TestPerFrameMetadata:
    def test_frame_index_increments_per_tick(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        for _ in range(3):
            rec.record_tick([_frame()], _result(readiness=[_readiness()]))
        rec.finalize(_outcome())

        frames = _read_meta(tmp_path)["frames"]
        assert [f["frame"] for f in frames] == [0, 1, 2]

    def test_per_camera_readiness_decision_captured(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0, 1], readiness_enabled=True,
        )
        rec.record_tick(
            [_frame(), _frame()],
            _result(readiness=[
                _readiness(ready=True, conf=0.93),
                _readiness(ready=False, conf=0.41),
            ]),
        )
        rec.finalize(_outcome())

        cams = _read_meta(tmp_path)["frames"][0]["cameras"]
        assert cams[0]["readiness"] == {"ready": True, "confidence": 0.93}
        assert cams[1]["readiness"] == {"ready": False, "confidence": 0.41}
        assert cams[0]["cam"] == 0
        assert cams[1]["cam"] == 1

    def test_readiness_marked_disabled_when_gating_off(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=False,
        )
        # The pipeline still emits synthetic ReadinessResult(ready=True, conf=1.0)
        # when gating is off; the recorder must NOT echo that as a "real" decision
        # because it would mislead a replay viewer.
        rec.record_tick(
            [_frame()],
            _result(readiness=[_readiness(ready=True, conf=1.0)]),
        )
        rec.finalize(_outcome())

        cams = _read_meta(tmp_path)["frames"][0]["cameras"]
        assert cams[0]["readiness"] == {"disabled": True}

    def test_bbox_serialized_with_confidence_and_rotation(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick(
            [_frame()],
            _result(
                readiness=[_readiness()],
                bboxes=[_bbox(coords=(10, 20, 30, 40), conf=0.88, rot=0.1)],
            ),
        )
        rec.finalize(_outcome())

        cam = _read_meta(tmp_path)["frames"][0]["cameras"][0]
        assert cam["bbox"] == {
            "bbox": [10, 20, 30, 40],
            "confidence": 0.88,
            "rotation": 0.1,
        }

    def test_bbox_is_null_when_pipeline_did_not_produce_one(self, tmp_path, fake_writer):
        # When readiness gate fails, process_frames returns InferenceResult with
        # bboxes=None entirely (no bbox stage ran).
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick([_frame()], _result(readiness=[_readiness(ready=False)]))
        rec.finalize(_outcome())

        cam = _read_meta(tmp_path)["frames"][0]["cameras"][0]
        assert cam["bbox"] is None
        assert cam["ocr"] is None
        assert cam["aggregation"] is None

    def test_bbox_is_null_when_individual_camera_has_no_detection(self, tmp_path, fake_writer):
        # Bbox stage ran but this camera detected nothing.
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0, 1], readiness_enabled=True,
        )
        rec.record_tick(
            [_frame(), _frame()],
            _result(
                readiness=[_readiness(), _readiness()],
                bboxes=[_bbox(coords=(1, 2, 3, 4)), SimpleNamespace(bbox=None, confidence=0.0, rotation=0.0)],
            ),
        )
        rec.finalize(_outcome())

        cams = _read_meta(tmp_path)["frames"][0]["cameras"]
        assert cams[0]["bbox"] is not None
        assert cams[1]["bbox"] is None

    def test_ocr_includes_text_confidence_and_digit_detections(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick(
            [_frame()],
            _result(
                readiness=[_readiness()],
                bboxes=[_bbox()],
                ocr=[_ocr(text="58", conf=0.81, digits=[
                    _digit(d=5, x1=1, y1=2, x2=3, y2=4, conf=0.75),
                    _digit(d=8, x1=5, y1=6, x2=7, y2=8, conf=0.91),
                ])],
            ),
        )
        rec.finalize(_outcome())

        ocr_meta = _read_meta(tmp_path)["frames"][0]["cameras"][0]["ocr"]
        assert ocr_meta["text"] == "58"
        assert ocr_meta["confidence"] == 0.81
        assert ocr_meta["digits"] == [
            {"digit": 5, "x1": 1, "y1": 2, "x2": 3, "y2": 4, "confidence": 0.75},
            {"digit": 8, "x1": 5, "y1": 6, "x2": 7, "y2": 8, "confidence": 0.91},
        ]

    def test_ocr_does_not_leak_image_arrays(self, tmp_path, fake_writer):
        # OCRResult carries crop_image and preprocessed_crop_image (numpy arrays).
        # Those must NOT end up in the JSON — they'd blow up the file size and
        # are not JSON-serializable anyway.
        ocr_with_images = SimpleNamespace(
            text="1", confidence=0.9,
            digit_detections=[],
            crop_image=np.zeros((10, 10, 3), dtype=np.uint8),
            preprocessed_crop_image=np.zeros((10, 10), dtype=np.uint8),
        )
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick(
            [_frame()],
            _result(readiness=[_readiness()], bboxes=[_bbox()], ocr=[ocr_with_images]),
        )
        rec.finalize(_outcome())

        ocr_meta = _read_meta(tmp_path)["frames"][0]["cameras"][0]["ocr"]
        assert "crop_image" not in ocr_meta
        assert "preprocessed_crop_image" not in ocr_meta

    def test_aggregation_serialized_per_camera(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick(
            [_frame()],
            _result(
                readiness=[_readiness()],
                bboxes=[_bbox()],
                ocr=[_ocr()],
                aggregation=[_agg(text="12", conf=0.6, status="accumulating", frames=3, reason="x")],
            ),
        )
        rec.finalize(_outcome())

        cam = _read_meta(tmp_path)["frames"][0]["cameras"][0]
        assert cam["aggregation"] == {
            "text": "12", "confidence": 0.6, "status": "accumulating",
            "frames_processed": 3, "reason": "x",
        }

    def test_fused_result_and_agg_status_at_tick_level(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0, 1], readiness_enabled=True,
        )
        rec.record_tick(
            [_frame(), _frame()],
            _result(
                readiness=[_readiness(), _readiness()],
                bboxes=[_bbox(), _bbox()],
                ocr=[_ocr(), _ocr()],
                aggregation=[
                    _agg(status="recognized", text="123"),
                    _agg(status="accumulating"),
                ],
                fused=_agg(text="123", conf=0.95, status="recognized", frames=10),
            ),
        )
        rec.finalize(_outcome(text="123"))

        tick = _read_meta(tmp_path)["frames"][0]
        assert tick["fused"] == {
            "text": "123", "confidence": 0.95, "status": "recognized",
            "frames_processed": 10, "reason": None,
        }
        # agg_status: any cam in a terminal state wins — matches
        # InferencePipeline.aggregation_status semantics.
        assert tick["agg_status"] == "recognized"

    def test_agg_status_accumulating_when_no_terminal_yet(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick(
            [_frame()],
            _result(
                readiness=[_readiness()], bboxes=[_bbox()], ocr=[_ocr()],
                aggregation=[_agg(status="accumulating")],
            ),
        )
        rec.finalize(_outcome())

        assert _read_meta(tmp_path)["frames"][0]["agg_status"] == "accumulating"

    def test_agg_status_null_when_no_aggregation_yet(self, tmp_path, fake_writer):
        # Early frames before any aggregation: result.aggregation is None.
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick([_frame()], _result(readiness=[_readiness()]))
        rec.finalize(_outcome())

        tick = _read_meta(tmp_path)["frames"][0]
        assert tick["agg_status"] is None
        assert tick["fused"] is None


class TestOutcome:
    def test_outcome_recorded_on_finalize(self, tmp_path, fake_writer):
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.record_tick([_frame()], _result(readiness=[_readiness()]))
        rec.finalize(_outcome(ok=False, text="unrecognized"))

        assert _read_meta(tmp_path)["outcome"] == {"ok": False, "text": "unrecognized"}

    def test_outcome_none_serialized_safely(self, tmp_path, fake_writer):
        # Exception in the OCR loop can leave outcome=None at finalize time. The
        # recorder must not crash and the artifact must still be valid JSON.
        rec = replay_recorder.ReplayRecorder(
            folder=str(tmp_path), cam_indices=[0], readiness_enabled=True,
        )
        rec.finalize(None)

        meta = _read_meta(tmp_path)
        assert meta["outcome"] == {"ok": False, "text": None}


class TestFolderHandling:
    def test_creates_folder_if_missing(self, tmp_path, fake_writer):
        target = tmp_path / "nested" / "capture"
        rec = replay_recorder.ReplayRecorder(
            folder=str(target), cam_indices=[0], readiness_enabled=True,
        )
        rec.finalize(_outcome())

        assert target.is_dir()
        assert next(target.glob("*-meta.json")).exists()
