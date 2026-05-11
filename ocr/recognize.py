"""OCR recognition flow: streams camera frames into the maxima_inference pipeline
until it reports a terminal aggregation state or a configurable frame budget runs out.
"""
import logging
from pathlib import Path
from typing import Iterator, List, Optional

import cv2
import numpy as np
from maxima_inference import InferencePipeline, validate_bundle

from ocr.usb_cams import get_cams, get_cap_format

logger = logging.getLogger(__name__)

DEFAULT_MAX_FRAMES = 400
DEFAULT_MODELS_ROOT = "./models"

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
    )
    pipeline.load_models()
    logger.info(
        "OCR pipeline loaded: bundle %s (schema %s); compat %s; device %s",
        pipeline.bundle_version,
        pipeline.bundle_schema_version,
        pipeline.wheel_compatibility,
        pipeline.device,
    )
    _pipeline = pipeline
    return _pipeline


def get_pipeline() -> InferencePipeline:
    """Return the cached pipeline. Caller must invoke `load_pipeline()` at startup."""
    if _pipeline is None:
        raise RuntimeError("OCR pipeline not loaded; call load_pipeline() at startup.")
    return _pipeline


def reset_pipeline() -> None:
    """Drop the cached pipeline. Primarily for tests; production never calls this."""
    global _pipeline
    _pipeline = None


def _iter_frames(config) -> Iterator[List[np.ndarray]]:
    """Yield one list-of-frames per tick (one frame per camera, BGR), until any
    camera stops producing or the consumer stops iterating."""
    cap_format = get_cap_format(config["camera"]["format"])
    cam_indices = get_cams(config, cap_format)
    if not cam_indices:
        logger.error("No cameras available for OCR.")
        return

    caps = [cv2.VideoCapture(idx, cap_format) for idx in cam_indices]
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


def recognize_cylinder(config) -> Optional[str]:
    """Run a single OCR trigger: stream frames into the pipeline until a terminal
    aggregation state, or until `ocr.max_frames` ticks have been processed.

    Returns the recognized cylinder text, or None on undetected / cap reached / no
    cameras available.
    """
    pipeline = get_pipeline()
    pipeline.reset()

    max_frames = config.get("ocr", {}).get("max_frames", DEFAULT_MAX_FRAMES)

    for _, frames in zip(range(max_frames), _iter_frames(config)):
        result = pipeline.process_frames(frames)
        if result.fused_result is not None and result.fused_result.text:
            return result.fused_result.text
        if pipeline.aggregation_status == "undetected":
            return None
    return None
