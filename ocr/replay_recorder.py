"""Persists an OCR recognition session to MP4 + JSON for replay & debugging.

One `ReplayRecorder` instance backs one `recognize_cylinder()` call. At
construction it stamps a session prefix `{YYYYMMDD-HHMMSS}` (same convention as
`ocr.usb_cams.capture_filename`). On the first `record_tick` it opens one
`cv2.VideoWriter` per camera; each subsequent tick appends a per-frame metadata
entry capturing readiness/bbox/OCR/aggregation decisions for every camera plus
the fused result. `finalize()` releases the writers and flushes
`{prefix}-meta.json` next to the MP4s — videos and metadata share the prefix so
a replay tool can pair them by filename.

Image arrays from `OCRResult` (`crop_image`, `preprocessed_crop_image`) are
deliberately NOT serialized — they would balloon the JSON and aren't replayable
without the original frame anyway.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2

from ocr.usb_cams import capture_filename

logger = logging.getLogger(__name__)

DEFAULT_FPS = 25
_FOURCC = cv2.VideoWriter_fourcc(*"mp4v")


class ReplayRecorder:
    def __init__(
        self,
        folder: str,
        cam_indices: List[int],
        readiness_enabled: bool,
        fps: int = DEFAULT_FPS,
    ) -> None:
        self._folder = Path(folder)
        self._folder.mkdir(parents=True, exist_ok=True)
        self._start_time = datetime.now()
        self._prefix = self._start_time.strftime("%Y%m%d-%H%M%S")
        self._cam_indices: List[int] = list(cam_indices)
        self._readiness_enabled = bool(readiness_enabled)
        self._fps = fps
        self._writers: Optional[List[Any]] = None
        self._frames_meta: List[Dict[str, Any]] = []
        self._crops_subdir = f"{self._prefix}-crops"
        self._crops_dir = self._folder / self._crops_subdir

    # --- Public API ---

    def record_tick(self, frames, result) -> None:
        """Persist one tick: write each camera's frame to its MP4 and record the
        model decisions for that tick."""
        if self._writers is None:
            self._open_writers(frames)
        for writer, frame in zip(self._writers, frames):
            writer.write(frame)
        self._frames_meta.append(self._encode_tick(result))

    def finalize(self, outcome) -> Path:
        """Release video writers and write the session JSON. Safe to call even
        if no ticks were recorded (e.g., the readiness gate failed before any
        camera produced a frame)."""
        if self._writers is not None:
            for w in self._writers:
                try:
                    w.release()
                except Exception:  # pragma: no cover — defensive
                    logger.exception("Video writer release failed")
        meta = {
            "session": self._prefix,
            "cameras": list(self._cam_indices),
            "fps": self._fps,
            "readiness_enabled": self._readiness_enabled,
            "frames": self._frames_meta,
            "outcome": self._encode_outcome(outcome),
        }
        meta_path = self._folder / f"{self._prefix}-meta.json"
        meta_path.write_text(json.dumps(meta, indent=2))
        logger.info("Replay recorder wrote session %s (%d frames)", self._prefix, len(self._frames_meta))
        return meta_path

    # --- Writer setup ---

    def _open_writers(self, frames) -> None:
        self._writers = []
        for cam_idx, frame in zip(self._cam_indices, frames):
            filename = capture_filename(self._start_time, cam_idx)
            path = self._folder / filename
            h, w = frame.shape[:2]
            writer = cv2.VideoWriter(str(path), _FOURCC, self._fps, (w, h))
            self._writers.append(writer)

    # --- Metadata encoding ---

    def _encode_tick(self, result) -> Dict[str, Any]:
        readiness = getattr(result, "readiness", None) or []
        n = len(readiness)
        bboxes = _pad_to(getattr(result, "bboxes", None), n)
        ocrs = _pad_to(getattr(result, "ocr", None), n)
        aggregations = _pad_to(getattr(result, "aggregation", None), n)
        fused = getattr(result, "fused_result", None)

        frame_idx = len(self._frames_meta)
        cams = []
        for i in range(n):
            cam_idx = self._cam_indices[i] if i < len(self._cam_indices) else i
            cams.append({
                "cam": cam_idx,
                "readiness": self._encode_readiness(readiness[i]),
                "bbox": self._encode_bbox(bboxes[i]),
                "ocr": self._encode_ocr(ocrs[i], frame_idx, cam_idx),
                "aggregation": self._encode_aggregation(aggregations[i]),
            })
        return {
            "frame": frame_idx,
            "cameras": cams,
            "agg_status": self._derive_agg_status(getattr(result, "aggregation", None)),
            "fused": self._encode_aggregation(fused),
        }

    def _encode_readiness(self, readiness) -> Optional[Dict[str, Any]]:
        if not self._readiness_enabled:
            return {"disabled": True}
        if readiness is None:
            return None
        return {
            "ready": bool(readiness.ready),
            "confidence": _as_float(readiness.confidence),
        }

    def _encode_bbox(self, bbox) -> Optional[Dict[str, Any]]:
        if bbox is None or getattr(bbox, "bbox", None) is None:
            return None
        return {
            "bbox": [int(v) for v in bbox.bbox],
            "confidence": _as_float(bbox.confidence),
            "rotation": _as_float(getattr(bbox, "rotation", 0.0)),
        }

    def _encode_ocr(self, ocr, frame_idx: int, cam_idx: int) -> Optional[Dict[str, Any]]:
        if ocr is None:
            return None
        digits = getattr(ocr, "digit_detections", None) or []
        crop_rel = self._persist_crop(getattr(ocr, "crop_image", None), frame_idx, cam_idx, "orig")
        prep_rel = self._persist_crop(getattr(ocr, "preprocessed_crop_image", None), frame_idx, cam_idx, "prep")
        return {
            "text": getattr(ocr, "text", None),
            "confidence": _as_float(getattr(ocr, "confidence", 0.0)),
            "digits": [
                {
                    "digit": int(d.digit),
                    "x1": int(d.x1),
                    "y1": int(d.y1),
                    "x2": int(d.x2),
                    "y2": int(d.y2),
                    "confidence": _as_float(d.confidence),
                }
                for d in digits
            ],
            "crop": crop_rel,
            "preproc": prep_rel,
        }

    def _persist_crop(self, image, frame_idx: int, cam_idx: int, kind: str) -> Optional[str]:
        """Write the crop to disk as PNG and return its path relative to the
        capture folder (forward-slash separated for portability), or None when
        the OCR stage didn't produce a crop for this frame/camera."""
        if image is None or getattr(image, "size", 0) == 0:
            return None
        if not self._crops_dir.exists():
            self._crops_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{frame_idx:04d}-{cam_idx}-{kind}.png"
        path = self._crops_dir / filename
        cv2.imwrite(str(path), image)
        return f"{self._crops_subdir}/{filename}"

    def _encode_aggregation(self, agg) -> Optional[Dict[str, Any]]:
        if agg is None:
            return None
        candidates = getattr(agg, "candidates", None) or []
        return {
            "text": getattr(agg, "text", None),
            "confidence": _as_float(getattr(agg, "confidence", 0.0)),
            "status": getattr(agg, "status", None),
            "frames_processed": getattr(agg, "frames_processed", None),
            "reason": getattr(agg, "reason", None),
            "expected_digits": int(getattr(agg, "expected_digits", 0) or 0),
            "candidates": [self._encode_candidate(c) for c in candidates],
        }

    def _encode_candidate(self, cand) -> Dict[str, Any]:
        return {
            "digit": int(cand.digit),
            "confidence": _as_float(cand.confidence),
            "frame_x": _maybe_int(getattr(cand, "frame_x", None)),
            "frame_y": _maybe_int(getattr(cand, "frame_y", None)),
            "crop_x": _maybe_int(getattr(cand, "crop_x", None)),
            "vote_count": int(getattr(cand, "vote_count", 0) or 0),
            "frame_count": int(getattr(cand, "frame_count", 0) or 0),
            "consistency": _as_float(getattr(cand, "consistency", 0.0)),
            "selected": bool(getattr(cand, "selected", False)),
        }

    def _derive_agg_status(self, aggregations) -> Optional[str]:
        if not aggregations:
            return None
        for a in aggregations:
            if a is not None and getattr(a, "status", None) in ("recognized", "undetected"):
                return a.status
        return "accumulating"

    def _encode_outcome(self, outcome) -> Dict[str, Any]:
        if outcome is None:
            return {"ok": False, "text": None}
        return {
            "ok": bool(getattr(outcome, "ok", False)),
            "text": getattr(outcome, "text", None),
        }


def _pad_to(seq, n: int):
    if seq is None:
        return [None] * n
    out = list(seq)
    if len(out) < n:
        out = out + [None] * (n - len(out))
    return out


def _as_float(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def _maybe_int(value) -> Optional[int]:
    if value is None:
        return None
    return int(value)
