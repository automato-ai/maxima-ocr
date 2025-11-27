from asyncio import sleep


import os
from datetime import datetime
from pathlib import Path
from typing import Final

import cv2


def get_cams(config):
    available_cameras = []
    for i in range(10): # Check indices from 0 to 9
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            print(f"Camera found at index: {i}")
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

    available_cameras = get_cams(config)
    print(f"Available camera indices: {available_cameras}")

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
                25,
                (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            )

        })

    frames_count = 0
    while frames_count < frames:
        for cap in caps:
            ret,frame = cap['cap'].read()
            cv2.imshow(f"USB Cam#{cap}", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                running = False
            cap['writer'].write(frame)
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
