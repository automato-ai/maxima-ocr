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


def _pipeline_returning(text=None, terminal=None):
    """Pipeline mock whose first process_frames returns the given fused text and sets the
    given aggregation_status afterward."""
    pipe = MagicMock()
    pipe.aggregation_status = None

    def step(_frames):
        pipe.aggregation_status = terminal
        fused = SimpleNamespace(text=text) if text is not None else None
        return SimpleNamespace(fused_result=fused)

    pipe.process_frames.side_effect = step
    return pipe


# ----- recognize_cylinder -----

class TestRecognizeCylinder:
    def test_returns_fused_text_when_recognized(self):
        pipe = _pipeline_returning(text="123ABC", terminal="recognized")
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter([[1], [2]])):
            assert recognize.recognize_cylinder({}) == "123ABC"

    def test_returns_none_when_aggregation_undetected(self):
        pipe = _pipeline_returning(text=None, terminal="undetected")
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter([[1], [2]])):
            assert recognize.recognize_cylinder({}) is None

    def test_returns_none_when_max_frames_exhausted_without_terminal(self):
        pipe = MagicMock()
        pipe.aggregation_status = None
        pipe.process_frames.return_value = SimpleNamespace(fused_result=None)

        config = {"ocr": {"max_frames": 3}}
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames",
                          return_value=iter([[1], [2], [3], [4], [5]])):
            assert recognize.recognize_cylinder(config) is None

        assert pipe.process_frames.call_count == 3

    def test_resets_pipeline_state_per_call(self):
        pipe = _pipeline_returning(text="X", terminal="recognized")
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter([[1]])):
            recognize.recognize_cylinder({})
        pipe.reset.assert_called_once()

    def test_default_max_frames_is_400(self):
        # Pin the documented default so a future refactor cannot silently change it.
        assert recognize.DEFAULT_MAX_FRAMES == 400

    def test_returns_none_when_no_frames_available(self):
        pipe = _pipeline_returning(text=None, terminal=None)
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter([])):
            assert recognize.recognize_cylinder({}) is None
        pipe.process_frames.assert_not_called()

    def test_passes_each_tick_frames_to_pipeline(self):
        pipe = MagicMock()
        pipe.aggregation_status = None
        pipe.process_frames.return_value = SimpleNamespace(fused_result=None)

        ticks = [["frame_a_t0", "frame_b_t0"], ["frame_a_t1", "frame_b_t1"]]
        with patch.object(recognize, "get_pipeline", return_value=pipe), \
             patch.object(recognize, "_iter_frames", return_value=iter(ticks)):
            recognize.recognize_cylinder({"ocr": {"max_frames": 10}})

        calls = [c.args[0] for c in pipe.process_frames.call_args_list]
        assert calls == ticks


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
