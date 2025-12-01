import os
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Final

import logging

import cv2
from cv2_enumerate_cameras import enumerate_cameras


logger = logging.getLogger(__name__)

def get_cams(config, format):
    available_cameras = []
    for camera_info in enumerate_cameras(format):
        logger.debug(f"Found camera #{camera_info.index}: {camera_info.name}\t pid={camera_info.pid}\t vid={camera_info.vid}\t path='{camera_info.path}'")
        if camera_info.pid is not None:
            available_cameras.append(camera_info.index)
    return available_cameras

DEFAULT_CONFIG: Final[object] = {
    'capture': {
        'folder': './capture',
        'frames': 10
    },
    'camera': {
        'format': "ANY"
    }
}


def get_cap_format(format):
    match format:
        case "ANY":
            return cv2.CAP_ANY
        case "DSHOW":
            return cv2.CAP_DSHOW
        case "QT":
            return cv2.CAP_QT
        case "MSMF":
            return cv2.CAP_MSMF
        case "OPENNI":
            return cv2.CAP_OPENNI
        case "FFMPEG":
            return cv2.CAP_FFMPEG
        case "OPENCV_MJPEG":
            return cv2.CAP_OPENCV_MJPEG
        case "V4L":
            return cv2.CAP_V4L
        case "V4L2":
            return cv2.CAP_V4L2
        case "INTEL_MFX":
            return cv2.CAP_INTEL_MFX
        case _:  # Default case
            logger.warning(f"Unsupported format: '{format}'. Roll back to 'ANY'.")
            return cv2.CAP_ANY


def capture_all_cams(config=DEFAULT_CONFIG):
    if config is None:
        config = DEFAULT_CONFIG
    capture_folder = config['capture']['folder']
    frames = config['capture']['frames']
    fps = 25
    format = get_cap_format(config['camera']['format'])

    available_cameras = get_cams(config, format)
    logger.info(f"Available camera for capturing: {available_cameras}")

    output_folder_path = Path(capture_folder)
    output_folder_path.mkdir(parents=True, exist_ok=True)
    start_time = datetime.now()

    caps = []
    for available_cap in available_cameras:
        cap = cv2.VideoCapture(available_cap, format)

        filename = capture_filename(start_time, available_cap)
        codec = cv2.VideoWriter_fourcc(*'mp4v')

        caps.append({
            'cap': cap,
            'writer': cv2.VideoWriter(
                os.path.join(capture_folder, filename),
                codec,
                fps,
                (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            )

        })

    frames_count = 0
    while frames_count < frames:
        start_time = datetime.now()
        # print(f"Grabbing frame {frames_count} @ {start_time}")
        for cap in caps:
            ret,frame = cap['cap'].read()
            cv2.imshow(f"USB Cam#{cap}", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                running = False
            cap['writer'].write(frame)
        spent_time = datetime.now() - start_time
        # print(f"Spent {spent_time} on frame {frames_count}")
        time_to_wait_micros = 1000000.0 / fps - spent_time.microseconds
        if time_to_wait_micros > 0:
            # print(f"waiting for {time_to_wait_micros}")
            sleep(time_to_wait_micros / 1000000)
        frames_count += 1

    for cap in caps:
        cap['cap'].release()
        cap['writer'].release()

    cv2.destroyAllWindows()


def capture_filename(start_time, cam_name):
    return f"{start_time.strftime('%Y%m%d-%H%M%S')}-{cam_name}.mp4"
