"""OCR recognition flow: streams camera frames into the maxima_inference pipeline
until it reports a terminal aggregation state or a configurable frame budget runs out.
"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import cv2
import numpy as np
from maxima_inference import InferencePipeline, validate_bundle

from ocr.usb_cams import get_cams, get_cap_format

logger = logging.getLogger(__name__)

DEFAULT_MAX_FRAMES = 400
DEFAULT_MODELS_ROOT = "./models"

ERR_CAMERAS_NOT_FOUND = "cameras not found"
ERR_CAMERAS_NOT_READY = "cameras not ready"
ERR_UNRECOGNIZED = "unrecognized"


@dataclass(frozen=True)
class RecognitionResult:
    """Outcome of a single OCR trigger.

    On success `ok` is True and `text` carries the recognized cylinder digits.
    On failure `ok` is False and `text` carries a human-readable description
    suitable for writing to the Modbus result registers (e.g.
    "cameras not found", "cameras not ready", "unrecognized").
    """
    ok: bool
    text: str


_pipeline: Optional[InferencePipeline] = None


def load_pipeline(config) -> InferencePipeline:
    """Validate the active bundle and construct the inference pipeline.

    Resolves `<models_root>/active.txt` to the active bundle directory, runs
    `validate_bundle()` against it, and raises `RuntimeError` with the structured
    issue list if the bundle is invalid. On success constructs the pipeline against
    `<bundle>/manifest.yaml`, loads weights, and caches the instance for `get_pipeline`.

    Called once at server startup; the cache is reused for the process lifetime.
    Idempotent — re-calls after success return the cached instance without re-validating.
    """
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    models_root = Path(config.get("models", {}).get("root", DEFAULT_MODELS_ROOT))
    active = (models_root / "active.txt").read_text().strip()
    bundle_dir = (models_root / active).resolve()

    report = validate_bundle(bundle_dir)
    if not report.ok:
        raise RuntimeError(
            "Bundle validation failed for {}:\n  - {}".format(
                bundle_dir, "\n  - ".join(report.issues)
            )
        )

    ocr_cfg = config.get("ocr", {})
    pipeline = InferencePipeline(
        config_path=bundle_dir / "manifest.yaml",
        min_ready_cams=ocr_cfg.get("min_ready_cams"),
        readiness_enabled=ocr_cfg.get("readiness_enabled", True),
    )
    pipeline.load_models()
    _apply_threshold_overrides(pipeline, ocr_cfg.get("thresholds") or {})
    logger.info(
        "OCR pipeline loaded: bundle %s (schema %s); compat %s; device %s",
        pipeline.bundle_version,
        pipeline.bundle_schema_version,
        pipeline.wheel_compatibility,
        pipeline.device,
    )
    _pipeline = pipeline
    return _pipeline


def _apply_threshold_overrides(pipeline, overrides: dict) -> None:
    """Apply config-level confidence threshold overrides on top of the bundle
    defaults loaded by `pipeline.load_models()`. Unknown keys are logged but
    ignored so a typo doesn't crash startup."""
    thresholds = pipeline.engine.thresholds
    for key, value in overrides.items():
        if value is None:
            continue
        if key not in thresholds:
            logger.warning(
                "Ignoring unknown threshold override %r (known: %s)",
                key, sorted(thresholds.keys()),
            )
            continue
        logger.info(
            "Threshold override: %s %s -> %s", key, thresholds[key], value,
        )
        thresholds[key] = value


def get_pipeline() -> InferencePipeline:
    """Return the cached pipeline. Caller must invoke `load_pipeline()` at startup."""
    if _pipeline is None:
        raise RuntimeError("OCR pipeline not loaded; call load_pipeline() at startup.")
    return _pipeline


def reset_pipeline() -> None:
    """Drop the cached pipeline. Primarily for tests; production never calls this."""
    global _pipeline
    _pipeline = None


def _open_camera_indices(config) -> Tuple[int, List[int]]:
    """Resolve the OpenCV capture format and enumerate camera indices for it."""
    cap_format = get_cap_format(config["camera"]["format"])
    return cap_format, get_cams(config, cap_format)


def _iter_frames(
    cap_format: int,
    cam_indices: List[int],
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Iterator[List[np.ndarray]]:
    """Yield one list-of-frames per tick (one frame per camera, BGR), until any
    camera stops producing or the consumer stops iterating.

    When `width`/`height` are provided, the requested capture resolution is
    applied to each cam. Cameras silently fall back to a supported mode if the
    request isn't honored; the actually-applied size is logged so an operator
    can spot a mismatch.
    """
    caps = []
    for idx in cam_indices:
        cap = cv2.VideoCapture(idx, cap_format)
        if not cap.isOpened():
            logger.error("Camera %d failed to open with the configured format.", idx)
            for opened in caps:
                opened.release()
            return
        default_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        default_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(
            "Camera %d resolution: default=%dx%d requested=%sx%s applied=%dx%d",
            idx, default_w, default_h, width, height, actual_w, actual_h,
        )
        caps.append(cap)
    try:
        while True:
            frames: List[np.ndarray] = []
            for cap in caps:
                ret, frame = cap.read()
                if not ret:
                    logger.error("Camera read failed; ending frame stream.")
                    return
                frames.append(frame)
            yield frames
    finally:
        for cap in caps:
            cap.release()


def recognize_cylinder(config) -> RecognitionResult:
    """Run a single OCR trigger and return a structured outcome.

    Success → `RecognitionResult(ok=True, text=<digits>)`.
    Failure → `RecognitionResult(ok=False, text=<description>)` where description
    is one of: "cameras not found" (no cameras enumerated), "cameras not ready"
    (readiness gate failed for the configured buffer), or "unrecognized" (pipeline
    terminated without recognition, or the frame budget was exhausted).
    """
    pipeline = get_pipeline()
    pipeline.reset()
    start = time.monotonic()

    cap_format, cam_indices = _open_camera_indices(config)
    max_frames = config.get("ocr", {}).get("max_frames", DEFAULT_MAX_FRAMES)
    cam_cfg = config.get("camera", {})
    width = cam_cfg.get("width")
    height = cam_cfg.get("height")
    logger.info(
        "OCR triggered: cameras=%s (%d enumerated), max_frames=%d",
        cam_indices, len(cam_indices), max_frames,
    )

    if not cam_indices:
        outcome = RecognitionResult(ok=False, text=ERR_CAMERAS_NOT_FOUND)
        _log_summary(outcome, frames=0, start=start)
        return outcome

    logger.info("OCR phase: streaming frames into pipeline")
    frames_seen = 0
    outcome: Optional[RecognitionResult] = None
    for _, frames in zip(
        range(max_frames),
        _iter_frames(cap_format, cam_indices, width=width, height=height),
    ):
        frames_seen += 1
        if frames_seen == 1:
            logger.info(
                "OCR first frame: shapes=%s dtypes=%s",
                [getattr(f, "shape", None) for f in frames],
                [str(getattr(f, "dtype", type(f).__name__)) for f in frames],
            )
        result = pipeline.process_frames(frames)
        if result.fused_result is not None and result.fused_result.text:
            outcome = RecognitionResult(ok=True, text=result.fused_result.text)
            break
        if pipeline.aggregation_status == "undetected":
            outcome = _undetected_outcome(result)
            break
    if outcome is None:
        if frames_seen < max_frames:
            logger.warning(
                "OCR frame stream ended early at %d/%d frames (camera read failed?)",
                frames_seen, max_frames,
            )
        outcome = RecognitionResult(ok=False, text=ERR_UNRECOGNIZED)

    _log_summary(outcome, frames=frames_seen, start=start)
    return outcome


def _log_summary(outcome: RecognitionResult, frames: int, start: float) -> None:
    logger.info(
        "OCR completed: text=%r ok=%s frames=%d elapsed=%.2fs",
        outcome.text, outcome.ok, frames, time.monotonic() - start,
    )


def _undetected_outcome(result) -> RecognitionResult:
    """Map an `undetected` aggregation result to the matching failure description.

    The pipeline tags readiness-gate failures with a reason string like
    "X/N cameras not ready for K consecutive frames"; presence of "not ready"
    in the reason separates the cams-not-ready case from a genuine no-detection.
    Per-camera diagnostics are logged so the operator can see how many digit
    candidates each camera accumulated before giving up.
    """
    aggregation = getattr(result, "aggregation", None) or []
    first_reason = ""
    for i, agg in enumerate(aggregation):
        if agg is None:
            continue
        candidates = getattr(agg, "candidates", None) or []
        logger.info(
            "OCR camera %d undetected: frames_processed=%s candidates=%d reason=%r",
            i, getattr(agg, "frames_processed", "?"), len(candidates),
            getattr(agg, "reason", None),
        )
        if not first_reason and getattr(agg, "reason", None):
            first_reason = agg.reason.lower()
    if "not ready" in first_reason:
        return RecognitionResult(ok=False, text=ERR_CAMERAS_NOT_READY)
    return RecognitionResult(ok=False, text=ERR_UNRECOGNIZED)
