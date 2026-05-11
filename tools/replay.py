#!/usr/bin/env python3
"""Replay a recorded OCR session for debugging and learning.

================================================================================
  Visual replay of one OCR trigger: synchronized video for every camera plus
  the model decisions captured frame-by-frame (readiness, bbox, OCR, aggregator
  candidates, fused result). Pairs 1:1 with the artifacts emitted by
  ocr.replay_recorder.
================================================================================

Usage:

    # Replay a specific session by its meta.json:
    python tools/replay.py path/to/20260511-153012-meta.json

    # Replay the latest session in a capture folder:
    python tools/replay.py path/to/capture/

Controls:

    space     pause / resume
    right     step forward one frame (paused)
    left      step backward one frame (paused)
    [ / ]     slow down / speed up
    home      jump to first frame
    q / ESC   quit

Layout (left-to-right, one column per camera):

    +-----------------------+
    |  camera video         |  bbox (cyan), bottom edge highlighted (red);
    |  with overlays        |  readiness chip in the top-left corner.
    +-----------------------+
    | original crop         |  vertical bars mark aggregator candidates;
    | (zoomed)              |  green = selected for the running result.
    +-----------------------+
    | preprocessed crop     |  OCR digit boxes + label/confidence pairs.
    | (zoomed)              |
    +-----------------------+

A header strip across the top reports session id, frame index, fused result,
and final outcome.
"""
import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


# ----- Layout constants ------------------------------------------------------

VIDEO_PANEL_W = 480
VIDEO_PANEL_H = 360
CROP_PANEL_H = 140
HEADER_H = 44
GUTTER = 10
LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
BG_COLOR = (24, 24, 24)
PANEL_BG = (40, 40, 40)
TEXT_COLOR = (235, 235, 235)
DIM_TEXT_COLOR = (160, 160, 160)

# Readiness chip
READY_COLOR = (0, 200, 0)
NOT_READY_COLOR = (0, 60, 220)
DISABLED_COLOR = (140, 140, 140)

# Bbox
BBOX_COLOR = (0, 220, 220)
BBOX_BOTTOM_COLOR = (60, 60, 255)
BBOX_THICKNESS = 2
BBOX_BOTTOM_THICKNESS = 3

# Candidates / OCR
SELECTED_CAND_COLOR = (60, 230, 60)
OTHER_CAND_COLOR = (180, 180, 60)
OCR_BOX_COLOR = (255, 200, 0)
OCR_TEXT_COLOR = (255, 255, 255)


# ----- Session / IO ----------------------------------------------------------

class Session:
    """Holds metadata + open video readers for one recorded OCR trigger."""

    def __init__(self, meta_path: Path) -> None:
        self.meta_path = meta_path.resolve()
        self.folder = self.meta_path.parent
        meta = json.loads(self.meta_path.read_text())
        self.session_prefix: str = meta["session"]
        self.cameras: List[int] = list(meta.get("cameras", []))
        self.fps: int = int(meta.get("fps", 25))
        self.readiness_enabled: bool = bool(meta.get("readiness_enabled", True))
        self.frames_meta: List[dict] = list(meta.get("frames", []))
        self.outcome: dict = meta.get("outcome", {"ok": False, "text": None})

        self.captures: List[cv2.VideoCapture] = []
        for cam in self.cameras:
            path = self.folder / f"{self.session_prefix}-{cam}.mp4"
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open video: {path}")
            self.captures.append(cap)

    def num_frames(self) -> int:
        if not self.captures:
            return len(self.frames_meta)
        video_frames = min(int(c.get(cv2.CAP_PROP_FRAME_COUNT)) for c in self.captures)
        meta_frames = len(self.frames_meta)
        return min(video_frames, meta_frames) if meta_frames else video_frames

    def read_frame(self, idx: int) -> List[np.ndarray]:
        out = []
        for cap in self.captures:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                frame = np.zeros((VIDEO_PANEL_H, VIDEO_PANEL_W, 3), dtype=np.uint8)
            out.append(frame)
        return out

    def frame_meta(self, idx: int) -> Optional[dict]:
        if 0 <= idx < len(self.frames_meta):
            return self.frames_meta[idx]
        return None

    def load_image(self, rel: Optional[str]) -> Optional[np.ndarray]:
        if not rel:
            return None
        # JSON uses forward slashes; the local filesystem handles either,
        # but be explicit so this works on Windows + POSIX.
        path = self.folder / Path(*rel.split("/"))
        if not path.is_file():
            return None
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        return img

    def close(self) -> None:
        for cap in self.captures:
            cap.release()


def resolve_meta_path(arg: Path) -> Path:
    """Accept either a meta.json or a folder. For a folder, pick the most recent
    *-meta.json by mtime."""
    if arg.is_file():
        return arg
    if arg.is_dir():
        candidates = sorted(arg.glob("*-meta.json"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise SystemExit(f"No *-meta.json found in {arg}")
        return candidates[-1]
    raise SystemExit(f"Not a file or directory: {arg}")


# ----- Drawing primitives ----------------------------------------------------

Placement = Tuple[int, int, float, int, int]  # (off_x, off_y, scale, src_w, src_h)


def fit_into(image: np.ndarray, max_w: int, max_h: int) -> Tuple[np.ndarray, Placement]:
    """Resize image to fit (max_w, max_h) preserving aspect ratio; pad the
    remainder with PANEL_BG so every panel has a stable footprint. Returns the
    canvas plus the placement transform so callers can map source-image
    coordinates onto the canvas (used for candidate / digit overlays)."""
    h, w = image.shape[:2]
    if w == 0 or h == 0:
        return _blank_panel(max_w, max_h), (0, 0, 1.0, w, h)
    scale = min(max_w / w, max_h / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    if resized.ndim == 2:
        resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)
    elif resized.shape[2] == 4:
        resized = cv2.cvtColor(resized, cv2.COLOR_BGRA2BGR)
    canvas = _blank_panel(max_w, max_h)
    off_x = (max_w - new_w) // 2
    off_y = (max_h - new_h) // 2
    canvas[off_y:off_y + new_h, off_x:off_x + new_w] = resized
    return canvas, (off_x, off_y, scale, w, h)


def _blank_panel(w: int, h: int) -> np.ndarray:
    panel = np.full((h, w, 3), PANEL_BG, dtype=np.uint8)
    return panel


def _put_label(img: np.ndarray, text: str, org: Tuple[int, int],
               color=TEXT_COLOR, scale: float = 0.5, thickness: int = 1) -> None:
    cv2.putText(img, text, org, LABEL_FONT, scale, color, thickness, cv2.LINE_AA)


def _put_label_with_bg(img: np.ndarray, text: str, org: Tuple[int, int],
                       fg=(255, 255, 255), bg=(0, 0, 0), scale: float = 0.5,
                       thickness: int = 1, pad: int = 3) -> None:
    (tw, th), baseline = cv2.getTextSize(text, LABEL_FONT, scale, thickness)
    x, y = org
    cv2.rectangle(img, (x, y - th - pad), (x + tw + 2 * pad, y + baseline), bg, -1)
    cv2.putText(img, text, (x + pad, y), LABEL_FONT, scale, fg, thickness, cv2.LINE_AA)


# ----- Video panel -----------------------------------------------------------

def draw_video_panel(frame: np.ndarray, cam_meta: dict, readiness_enabled: bool) -> np.ndarray:
    """Resize frame to the video tile, then overlay readiness chip + bbox."""
    panel, (off_x, off_y, scale, _, _) = fit_into(frame, VIDEO_PANEL_W, VIDEO_PANEL_H)

    bbox = cam_meta.get("bbox")
    if bbox is not None:
        _draw_obb(panel, bbox, off_x, off_y, scale)

    readiness = cam_meta.get("readiness")
    cam_label = f"cam {cam_meta.get('cam')}"
    _draw_readiness_chip(panel, readiness, readiness_enabled, cam_label)
    return panel


def _draw_obb(panel: np.ndarray, bbox_meta: dict, off_x: int, off_y: int, scale: float) -> None:
    """Draw the bbox quad on the panel. The pipeline emits bbox as (x1,y1,x2,y2)
    even for YOLO-OBB; the OBB shape is reconstructed by rotating that AABB
    around its center by `rotation` radians (CCW per maxima_inference)."""
    x1, y1, x2, y2 = bbox_meta["bbox"]
    rotation = float(bbox_meta.get("rotation", 0.0) or 0.0)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
    # Corner offsets before rotation (image coords: y grows downward).
    corners = [
        (-w / 2, -h / 2),   # TL
        (+w / 2, -h / 2),   # TR
        (+w / 2, +h / 2),   # BR
        (-w / 2, +h / 2),   # BL
    ]
    # maxima_inference uses "positive = CCW", which in image-coords (y-down)
    # rotates points clockwise visually. The math below uses the standard
    # rotation matrix and yields the same convention pipeline_core consumes.
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    pts: List[Tuple[int, int]] = []
    for dx, dy in corners:
        rx = dx * cos_r - dy * sin_r
        ry = dx * sin_r + dy * cos_r
        px = int(off_x + (cx + rx) * scale)
        py = int(off_y + (cy + ry) * scale)
        pts.append((px, py))
    quad = np.array(pts, dtype=np.int32)
    cv2.polylines(panel, [quad], isClosed=True, color=BBOX_COLOR, thickness=BBOX_THICKNESS)
    # Highlight the BL-BR edge so the operator can read text orientation
    # at a glance ("which way is down").
    cv2.line(panel, pts[3], pts[2], BBOX_BOTTOM_COLOR, BBOX_BOTTOM_THICKNESS)
    # Confidence label near the top-left corner of the OBB.
    conf = bbox_meta.get("confidence")
    if conf is not None:
        _put_label_with_bg(panel, f"bbox {conf:.2f}",
                           (pts[0][0], max(0, pts[0][1] - 4)),
                           bg=(0, 0, 0))


def _draw_readiness_chip(panel: np.ndarray, readiness: Optional[dict],
                         readiness_enabled: bool, cam_label: str) -> None:
    if not readiness_enabled or (readiness is not None and readiness.get("disabled")):
        text = f"{cam_label}  readiness OFF"
        color = DISABLED_COLOR
    elif readiness is None:
        text = f"{cam_label}  readiness ?"
        color = DISABLED_COLOR
    else:
        ready = bool(readiness.get("ready"))
        conf = float(readiness.get("confidence", 0.0))
        text = f"{cam_label}  {'READY' if ready else 'NOT READY'} {conf:.2f}"
        color = READY_COLOR if ready else NOT_READY_COLOR
    _put_label_with_bg(panel, text, (8, 22), fg=(255, 255, 255), bg=color,
                       scale=0.55, thickness=1, pad=4)


# ----- Crop panels -----------------------------------------------------------

def draw_orig_crop_panel(session: Session, cam_meta: dict) -> np.ndarray:
    ocr_meta = cam_meta.get("ocr") or {}
    crop_rel = ocr_meta.get("crop")
    image = session.load_image(crop_rel)
    if image is None:
        return _empty_crop_panel("no crop (bbox missed)")

    panel, (off_x, off_y, scale, orig_w, _) = fit_into(image, VIDEO_PANEL_W, CROP_PANEL_H)

    agg = cam_meta.get("aggregation")
    if agg:
        for cand in agg.get("candidates", []) or []:
            crop_x = cand.get("crop_x")
            if crop_x is None or orig_w <= 0:
                continue
            x = int(off_x + crop_x * scale)
            color = SELECTED_CAND_COLOR if cand.get("selected") else OTHER_CAND_COLOR
            cv2.line(panel, (x, off_y), (x, off_y + int(image.shape[0] * scale)),
                     color, 2)
            label = f"{cand.get('digit')}  {cand.get('confidence', 0):.2f}"
            _put_label(panel, label, (x + 3, off_y + 14), color=color, scale=0.45)

    _put_label_with_bg(panel, "original crop  (aggregator candidates)",
                       (6, panel.shape[0] - 6),
                       fg=(220, 220, 220), bg=(0, 0, 0), scale=0.4)
    return panel


def draw_prep_crop_panel(session: Session, cam_meta: dict) -> np.ndarray:
    ocr_meta = cam_meta.get("ocr") or {}
    prep_rel = ocr_meta.get("preproc")
    image = session.load_image(prep_rel)
    if image is None:
        return _empty_crop_panel("no preprocessed crop")

    panel, (off_x, off_y, scale, _, _) = fit_into(image, VIDEO_PANEL_W, CROP_PANEL_H)
    for d in ocr_meta.get("digits", []) or []:
        x1 = int(off_x + d["x1"] * scale)
        y1 = int(off_y + d["y1"] * scale)
        x2 = int(off_x + d["x2"] * scale)
        y2 = int(off_y + d["y2"] * scale)
        cv2.rectangle(panel, (x1, y1), (x2, y2), OCR_BOX_COLOR, 1)
        label = f"{d['digit']} {d.get('confidence', 0):.2f}"
        _put_label_with_bg(panel, label, (x1, max(10, y1 - 2)),
                           fg=OCR_TEXT_COLOR, bg=(0, 0, 0), scale=0.4)

    text = ocr_meta.get("text")
    conf = ocr_meta.get("confidence", 0.0)
    footer = f"preprocessed crop  OCR={text!r}  conf={conf:.2f}"
    _put_label_with_bg(panel, footer, (6, panel.shape[0] - 6),
                       fg=(220, 220, 220), bg=(0, 0, 0), scale=0.4)
    return panel


def _empty_crop_panel(text: str) -> np.ndarray:
    panel = _blank_panel(VIDEO_PANEL_W, CROP_PANEL_H)
    _put_label(panel, text, (10, CROP_PANEL_H // 2), color=DIM_TEXT_COLOR, scale=0.6)
    return panel


# ----- Composition -----------------------------------------------------------

def compose_layout(session: Session, frame_idx: int, frames: List[np.ndarray],
                   paused: bool, speed: float) -> np.ndarray:
    cams_meta = (session.frame_meta(frame_idx) or {}).get("cameras", [])

    panels: List[np.ndarray] = []
    for i, frame in enumerate(frames):
        cam_meta = cams_meta[i] if i < len(cams_meta) else {"cam": session.cameras[i]}
        video = draw_video_panel(frame, cam_meta, session.readiness_enabled)
        orig = draw_orig_crop_panel(session, cam_meta)
        prep = draw_prep_crop_panel(session, cam_meta)
        column = _stack_column([video, orig, prep])
        panels.append(column)

    body = _row(panels)
    header = _draw_header(session, frame_idx, body.shape[1], paused, speed)
    return np.vstack([header, body])


def _stack_column(panels: List[np.ndarray]) -> np.ndarray:
    gap = np.full((GUTTER, VIDEO_PANEL_W, 3), BG_COLOR, dtype=np.uint8)
    stacked: List[np.ndarray] = []
    for i, p in enumerate(panels):
        if i > 0:
            stacked.append(gap)
        stacked.append(p)
    return np.vstack(stacked)


def _row(columns: List[np.ndarray]) -> np.ndarray:
    h = max(c.shape[0] for c in columns)
    padded = []
    for col in columns:
        if col.shape[0] < h:
            pad = np.full((h - col.shape[0], col.shape[1], 3), BG_COLOR, dtype=np.uint8)
            col = np.vstack([col, pad])
        padded.append(col)
    gap = np.full((h, GUTTER, 3), BG_COLOR, dtype=np.uint8)
    out: List[np.ndarray] = []
    for i, col in enumerate(padded):
        if i > 0:
            out.append(gap)
        out.append(col)
    return np.hstack(out)


def _draw_header(session: Session, frame_idx: int, width: int,
                 paused: bool, speed: float) -> np.ndarray:
    header = np.full((HEADER_H, width, 3), BG_COLOR, dtype=np.uint8)
    total = session.num_frames()
    fm = session.frame_meta(frame_idx) or {}
    fused = fm.get("fused")
    fused_txt = (
        f"fused={fused.get('text')!r} ({fused.get('status')})"
        if fused else "fused=-"
    )
    outcome = session.outcome
    outcome_txt = f"outcome=({'OK' if outcome.get('ok') else 'FAIL'}) {outcome.get('text')!r}"
    state = "PAUSED" if paused else f"PLAY x{speed:.1f}"
    line = (
        f"session={session.session_prefix}  cams={session.cameras}  "
        f"frame={frame_idx + 1}/{total}  agg={fm.get('agg_status')}  "
        f"{fused_txt}  {outcome_txt}  [{state}]"
    )
    _put_label(header, line, (12, 28), color=TEXT_COLOR, scale=0.55, thickness=1)
    return header


# ----- Main loop -------------------------------------------------------------

def play(session: Session) -> None:
    total = session.num_frames()
    if total <= 0:
        raise SystemExit("Session has no frames to replay.")
    win = f"Maxima OCR replay — {session.session_prefix}"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    idx = 0
    paused = False
    speed = 1.0
    base_delay_ms = max(1, int(1000 / session.fps))
    while True:
        frames = session.read_frame(idx)
        canvas = compose_layout(session, idx, frames, paused, speed)
        cv2.imshow(win, canvas)
        wait_ms = base_delay_ms if not paused else 0
        wait_ms = max(1, int(wait_ms / max(0.1, speed))) if not paused else 0
        key = cv2.waitKey(wait_ms) & 0xFFFF
        action = _key_action(key)
        if action == "quit":
            break
        if action == "toggle_pause":
            paused = not paused
            continue
        if action == "step_forward":
            paused = True
            idx = min(total - 1, idx + 1)
            continue
        if action == "step_back":
            paused = True
            idx = max(0, idx - 1)
            continue
        if action == "home":
            idx = 0
            continue
        if action == "faster":
            speed = min(8.0, speed * 1.5)
            continue
        if action == "slower":
            speed = max(0.25, speed / 1.5)
            continue
        if not paused:
            idx += 1
            if idx >= total:
                paused = True
                idx = total - 1
    cv2.destroyWindow(win)


def _key_action(key: int) -> Optional[str]:
    """Translate cv2.waitKey return values to actions. Arrow keys differ by
    platform (Windows reports 2424832 / 2555904 via WaitKeyEx, but waitKey
    masks to 0xFFFF and reports 81/83 on some builds). We accept both."""
    if key == 0xFFFF or key == 255:
        return None
    # Note: ord('Q') == 81 collides with one common cv2 keycode for "left
    # arrow"; we only accept lowercase q for quit so the arrow keys work
    # regardless of platform.
    if key in (27, ord('q')):
        return "quit"
    if key == ord(' '):
        return "toggle_pause"
    if key in (ord('['),):
        return "slower"
    if key in (ord(']'),):
        return "faster"
    # right arrow: Windows = 0x27, common cv2 = 83; left arrow: 0x25 / 81.
    if key in (0x27, 83, 2555904):
        return "step_forward"
    if key in (0x25, 81, 2424832):
        return "step_back"
    if key in (0x24, 80, 2359296, ord('0')):
        return "home"
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a recorded OCR session.",
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Path to a {prefix}-meta.json or to a capture folder.",
    )
    args = parser.parse_args(argv)
    meta_path = resolve_meta_path(args.target)
    session = Session(meta_path)
    try:
        play(session)
    finally:
        session.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
