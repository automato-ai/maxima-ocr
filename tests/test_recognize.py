from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ocr import recognize


def _ok_report():
    return SimpleNamespace(ok=True, issues=[])


def _bad_report(issues):
    return SimpleNamespace(ok=False, issues=list(issues))


@pytest.fixture(autouse=True)
def _isolate_pipeline_singleton():
    recognize.reset_pipeline()
    yield
    recognize.reset_pipeline()


def _pipeline_returning(text=None, terminal=None, undetected_reason=None):
    """Pipeline mock whose first process_frames returns the given fused text and sets the
    given aggregation_status afterward. For terminal="undetected", `undetected_reason`
    populates `result.aggregation[0].reason` so the caller can distinguish failure modes.
    """
    pipe = MagicMock()
    pipe.aggregation_status = None

    def step(_frames):
        pipe.aggregation_status = terminal
        fused = SimpleNamespace(text=text) if text is not None else None
        aggregation = None
        if terminal == "undetected":
            aggregation = [SimpleNamespace(reason=undetected_reason)]
        return SimpleNamespace(fused_result=fused, aggregation=aggregation)

    pipe.process_frames.side_effect = step
    return pipe


@pytest.fixture
def cams_available(monkeypatch):
    """Default cam discovery to a single-camera environment so tests that don't care
    about cam enumeration still pass the upfront 'cameras not found' guard."""
    monkeypatch.setattr(recognize, "_open_camera_indices", lambda _cfg: (0, [0]))


# ----- recognize_cylinder -----

class TestRecognizeCylinder:
    def test_returns_success_result_when_recognized(self, cams_available):
        pipe = _pipeline_returning(text="123ABC", terminal="recognized")
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter([[1], [2]])):
            result = recognize.recognize_cylinder({})

        assert result == recognize.RecognitionResult(ok=True, text="123ABC")

    def test_returns_unrecognized_when_aggregation_undetected(self, cams_available):
        pipe = _pipeline_returning(text=None, terminal="undetected")
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter([[1], [2]])):
            result = recognize.recognize_cylinder({})

        assert result == recognize.RecognitionResult(
            ok=False, text=recognize.ERR_UNRECOGNIZED
        )

    def test_returns_cameras_not_ready_when_readiness_gate_fails(self, cams_available):
        # The pipeline reports "undetected" with a "X/N cameras not ready ..." reason
        # when the readiness gate fails for the configured buffer.
        pipe = _pipeline_returning(
            text=None,
            terminal="undetected",
            undetected_reason="2/3 cameras not ready for 200 consecutive frames",
        )
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter([[1]])):
            result = recognize.recognize_cylinder({})

        assert result == recognize.RecognitionResult(
            ok=False, text=recognize.ERR_CAMERAS_NOT_READY
        )

    def test_returns_unrecognized_when_max_frames_exhausted_without_terminal(
        self, cams_available
    ):
        pipe = MagicMock()
        pipe.aggregation_status = None
        pipe.process_frames.return_value = SimpleNamespace(
            fused_result=None, aggregation=None
        )

        config = {"ocr": {"max_frames": 3}}
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames",
                          return_value=iter([[1], [2], [3], [4], [5]])):
            result = recognize.recognize_cylinder(config)

        assert result == recognize.RecognitionResult(
            ok=False, text=recognize.ERR_UNRECOGNIZED
        )
        assert pipe.process_frames.call_count == 3

    def test_resets_pipeline_state_per_call(self, cams_available):
        pipe = _pipeline_returning(text="X", terminal="recognized")
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter([[1]])):
            recognize.recognize_cylinder({})
        pipe.reset.assert_called_once()

    def test_default_max_frames_is_400(self):
        # Pin the documented default so a future refactor cannot silently change it.
        assert recognize.DEFAULT_MAX_FRAMES == 400

    def test_returns_cameras_not_found_when_no_cams_enumerated(self, monkeypatch):
        monkeypatch.setattr(recognize, "_open_camera_indices", lambda _cfg: (0, []))
        pipe = _pipeline_returning(text=None, terminal=None)
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames") as iter_mock:
            result = recognize.recognize_cylinder({})

        assert result == recognize.RecognitionResult(
            ok=False, text=recognize.ERR_CAMERAS_NOT_FOUND
        )
        # Cam check fails upfront — pipeline and frame iteration must never run.
        pipe.process_frames.assert_not_called()
        iter_mock.assert_not_called()

    def test_passes_each_tick_frames_to_pipeline(self, cams_available):
        pipe = MagicMock()
        pipe.aggregation_status = None
        pipe.process_frames.return_value = SimpleNamespace(
            fused_result=None, aggregation=None
        )

        ticks = [["frame_a_t0", "frame_b_t0"], ["frame_a_t1", "frame_b_t1"]]
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter(ticks)):
            recognize.recognize_cylinder({"ocr": {"max_frames": 10}})

        calls = [c.args[0] for c in pipe.process_frames.call_args_list]
        assert calls == ticks

    def test_camera_resolution_passed_to_iter_frames(self, cams_available):
        pipe = _pipeline_returning(text="OK", terminal="recognized")
        config = {"camera": {"width": 1280, "height": 720}}
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames",
                          return_value=iter([[1]])) as iter_mock:
            recognize.recognize_cylinder(config)

        assert iter_mock.call_args.kwargs.get("width") == 1280
        assert iter_mock.call_args.kwargs.get("height") == 720

    def test_camera_resolution_defaults_to_none_when_unset(self, cams_available):
        pipe = _pipeline_returning(text="OK", terminal="recognized")
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames",
                          return_value=iter([[1]])) as iter_mock:
            recognize.recognize_cylinder({})

        assert iter_mock.call_args.kwargs.get("width") is None
        assert iter_mock.call_args.kwargs.get("height") is None


# ----- load_pipeline -----

class TestLoadPipeline:
    def _write_bundle(self, root, version="20260507"):
        bundle = root / version
        bundle.mkdir()
        (bundle / "manifest.yaml").write_text("# stub manifest")
        (root / "active.txt").write_text(f"{version}\n")
        return bundle / "manifest.yaml"

    def test_constructs_pipeline_from_active_bundle(self, tmp_path):
        manifest = self._write_bundle(tmp_path)
        config = {
            "models": {"root": str(tmp_path)},
            "ocr": {"min_ready_cams": 2},
        }

        pipe = MagicMock()
        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=pipe) as ctor:
            result = recognize.load_pipeline(config)

        assert result is pipe
        ctor.assert_called_once()
        kwargs = ctor.call_args.kwargs
        assert kwargs["config_path"] == manifest.resolve()
        assert kwargs["min_ready_cams"] == 2
        pipe.load_models.assert_called_once()

    def test_validates_bundle_before_constructing_pipeline(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {"models": {"root": str(tmp_path)}}

        with patch.object(recognize, "validate_bundle", return_value=_ok_report()) as vb, \
             patch.object(recognize, "InferencePipeline", return_value=MagicMock()):
            recognize.load_pipeline(config)

        # validate_bundle gets the bundle directory (parent of manifest.yaml).
        vb.assert_called_once()
        bundle_arg = vb.call_args.args[0]
        assert bundle_arg.resolve() == (tmp_path / "20260507").resolve()

    def test_invalid_bundle_raises_with_issues_and_skips_pipeline(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {"models": {"root": str(tmp_path)}}
        issues = ["readiness model md5 mismatch", "manifest missing wheel_compatibility"]

        with patch.object(recognize, "validate_bundle", return_value=_bad_report(issues)), \
             patch.object(recognize, "InferencePipeline") as ctor:
            with pytest.raises(RuntimeError) as exc:
                recognize.load_pipeline(config)

        message = str(exc.value)
        for issue in issues:
            assert issue in message
        ctor.assert_not_called()

    def test_caches_pipeline_across_calls(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {"models": {"root": str(tmp_path)}}

        pipe = MagicMock()
        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=pipe) as ctor:
            first = recognize.load_pipeline(config)
            second = recognize.load_pipeline(config)

        assert first is second
        ctor.assert_called_once()

    def test_models_root_defaults_to_models_dir(self, tmp_path, monkeypatch):
        # Default ./models relative to CWD.
        monkeypatch.chdir(tmp_path)
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        manifest = self._write_bundle(models_dir)

        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=MagicMock()) as ctor:
            recognize.load_pipeline({})

        assert ctor.call_args.kwargs["config_path"] == manifest.resolve()

    def test_min_ready_cams_passes_none_when_unset(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {"models": {"root": str(tmp_path)}}

        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=MagicMock()) as ctor:
            recognize.load_pipeline(config)

        assert ctor.call_args.kwargs["min_ready_cams"] is None

    def test_readiness_enabled_defaults_to_true_when_unset(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {"models": {"root": str(tmp_path)}}

        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=MagicMock()) as ctor:
            recognize.load_pipeline(config)

        assert ctor.call_args.kwargs["readiness_enabled"] is True

    def test_readiness_enabled_false_disables_gating(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {
            "models": {"root": str(tmp_path)},
            "ocr": {"readiness_enabled": False},
        }

        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=MagicMock()) as ctor:
            recognize.load_pipeline(config)

        assert ctor.call_args.kwargs["readiness_enabled"] is False

    def test_threshold_overrides_applied_to_engine(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {
            "models": {"root": str(tmp_path)},
            "ocr": {
                "thresholds": {
                    "bbox_confidence": 0.5,
                    "ocr_confidence": 0.8,
                },
            },
        }
        pipe = MagicMock()
        pipe.engine.thresholds = {
            "readiness_confidence": 0.75,
            "bbox_confidence": 0.75,
            "ocr_confidence": 0.75,
        }

        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=pipe):
            recognize.load_pipeline(config)

        # Overridden keys take the config value; unset keys keep the bundle default.
        assert pipe.engine.thresholds["bbox_confidence"] == 0.5
        assert pipe.engine.thresholds["ocr_confidence"] == 0.8
        assert pipe.engine.thresholds["readiness_confidence"] == 0.75

    def test_no_threshold_overrides_when_unset(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {"models": {"root": str(tmp_path)}}
        pipe = MagicMock()
        pipe.engine.thresholds = {
            "readiness_confidence": 0.75,
            "bbox_confidence": 0.75,
            "ocr_confidence": 0.75,
        }

        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=pipe):
            recognize.load_pipeline(config)

        assert pipe.engine.thresholds == {
            "readiness_confidence": 0.75,
            "bbox_confidence": 0.75,
            "ocr_confidence": 0.75,
        }

    def test_unknown_threshold_key_is_ignored_with_warning(self, tmp_path, caplog):
        self._write_bundle(tmp_path)
        config = {
            "models": {"root": str(tmp_path)},
            "ocr": {"thresholds": {"bbox_confidance": 0.5}},  # typo
        }
        pipe = MagicMock()
        pipe.engine.thresholds = {"bbox_confidence": 0.75}

        with caplog.at_level("WARNING", logger=recognize.logger.name), \
             patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=pipe):
            recognize.load_pipeline(config)

        assert pipe.engine.thresholds == {"bbox_confidence": 0.75}
        assert any("bbox_confidance" in r.message for r in caplog.records)

    def test_reset_pipeline_drops_cached_instance(self, tmp_path):
        self._write_bundle(tmp_path)
        config = {"models": {"root": str(tmp_path)}}

        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", side_effect=[MagicMock(), MagicMock()]) as ctor:
            recognize.load_pipeline(config)
            recognize.reset_pipeline()
            recognize.load_pipeline(config)

        assert ctor.call_count == 2


# ----- get_pipeline (cache accessor) -----

class TestGetPipeline:
    def test_raises_when_pipeline_not_loaded(self):
        with pytest.raises(RuntimeError):
            recognize.get_pipeline()

    def test_returns_cached_pipeline_after_load(self, tmp_path):
        bundle = tmp_path / "20260507"
        bundle.mkdir()
        (bundle / "manifest.yaml").write_text("# stub")
        (tmp_path / "active.txt").write_text("20260507\n")
        config = {"models": {"root": str(tmp_path)}}

        pipe = MagicMock()
        with patch.object(recognize, "validate_bundle", return_value=_ok_report()), \
             patch.object(recognize, "InferencePipeline", return_value=pipe):
            recognize.load_pipeline(config)

        assert recognize.get_pipeline() is pipe
