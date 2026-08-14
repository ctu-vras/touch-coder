"""
adapters/video_probe.py
cv2-based video metadata probing, pulled out of video_model.Video so the
model constructor does no I/O — the controller probes once and passes the
values in.
"""

import cv2


def probe(video_path):
    """Open `video_path` once and return (raw_frame_count, fps).

    `raw_frame_count` is cv2's CAP_PROP_FRAME_COUNT as-is; the caller derives
    the last frame INDEX (count - 1) for Video.total_frames, exactly as the
    old Video.get_total_frames did.
    """
    cap = cv2.VideoCapture(video_path)
    is_opened = cap.isOpened()
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    print(f"INFO: VideoCapture opened: {is_opened}")
    print(f"INFO: OpenCV frame count: {total_frames}")
    print(f"INFO: OpenCV FPS: {fps:.3f}")
    return total_frames, fps
