import cv2
# --- top of video_model.py ---
import sys
from typing import Dict
from data_utils import empty_bundle, FrameBundle
from collections import UserDict

class LimbView(UserDict):
    def __init__(self, frames: Dict[int, FrameBundle], limb: str):
        super().__init__()
        self._frames = frames
        self._limb = limb

    def __getitem__(self, frame: int):
        b = self._frames.setdefault(frame, empty_bundle())
        return b[self._limb]

    def __setitem__(self, frame: int, rec):
        b = self._frames.setdefault(frame, empty_bundle())
        b[self._limb] = rec

    def get(self, frame, default=None):
        b = self._frames.get(frame)
        return (b[self._limb] if b and self._limb in b else default)

    def setdefault(self, frame, rec):
        if frame not in self._frames:
            self._frames[frame] = empty_bundle()
        if not self._frames[frame][self._limb]:
            self._frames[frame][self._limb] = rec
        return self._frames[frame][self._limb]

class Video:
    def __init__(self, video_path):
        self.video_path = video_path
        self.current_frame = 0  # Starting at frame 0
        self.current_frame_zone = 0
        self.number_frames_in_zone = 100
        self.video_name = None
        self.total_frames = self.get_total_frames()
        self.number_zones = int(self.total_frames/self.number_frames_in_zone) + 1
        self.frames_dir = None
        self.data = {}
        self.data_path_to_csv = None
        self.dots = []
        self.frames: Dict[int, FrameBundle] = {}
        # Expose limb views so existing code (dataRH/LH/RL/LL) keeps working:
        self.dataRH = LimbView(self.frames, "RH")
        self.dataLH = LimbView(self.frames, "LH")
        self.dataRL = LimbView(self.frames, "RL")
        self.dataLL = LimbView(self.frames, "LL")

        self.is_touchRH = False
        self.is_touchLH = False
        self.is_touchRL = False
        self.is_touchLL = False
        self.touch_to_next_zone = [False for _ in range(self.number_zones)]
        self.last_green = [(10, 10),(5, 5),(50, 50)]
        self.play = False
        self.frame_rate = None
        self.parameter_button1_state_dict = {}
        self.parameter_button2_state_dict = {}
        self.parameter_button3_state_dict = {}
        self.dataNotes_path_to_csv = None
        

        if sys.platform.startswith("win"):
            self.program_version = "7.5.7 (Windows)"
        elif sys.platform.startswith("linux"):
            self.program_version = "7.5.7 (Linux)"
        else:
            self.program_version = "7.5.7 (Unknown OS)"
        print("INFO: Program version:", self.program_version)
        self.parameter1_name = None
        self.parameter2_name = None
        self.parameter3_name = None
        self.clothes_file_path = None
        self.notes = {}
        self.limb_parameter1 = {}  # RH, LH, RL, LL each has its own entry
        self.limb_parameter2 = {}
        self.limb_parameter3 = {}

    def get_total_frames(self):
        cap = cv2.VideoCapture(self.video_path)
        is_opened = cap.isOpened()
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        print(f"INFO: VideoCapture opened: {is_opened}")
        print(f"INFO: OpenCV frame count: {total_frames}")
        print(f"INFO: OpenCV FPS: {fps:.3f}")
        return total_frames - 1

    def get_frame(self, frame_number):
        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        success, frame = cap.read()
        cap.release()
        if success:
            return frame
        else:
            return None
