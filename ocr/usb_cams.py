import os
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Final

import logging

import cv2

logger = logging.getLogger(__name__)

def get_cams(config):
    available_cameras = []
    for i in range(10): # Check indices from 0 to 9
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            logger.debug(f"Camera found at index: {i}")
            available_cameras.append(i)
            cap.release() # Release the camera after checking
        else:
            cap.release() # Release if not opened

    return available_cameras

DEFAULT_CONFIG: Final[object] = {
    'capture': {
        'folder': './capture',
        'frames': 10
    }
}

def capture_all_cams(config=DEFAULT_CONFIG):
    if config is None:
        config = DEFAULT_CONFIG
    capture_folder = config['capture']['folder']
    frames = config['capture']['frames']
    fps = 25

    available_cameras = get_cams(config)
    logger.info(f"Available camera indices: {available_cameras}")

    output_folder_path = Path(capture_folder)
    output_folder_path.mkdir(parents=True, exist_ok=True)
    start_time = datetime.now()

    caps = []
    for available_cap in available_cameras:
        cap = cv2.VideoCapture(available_cap)

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


if __name__ == '__main__':
    capture_all_cams()
    sleep(5)
    capture_all_cams()
