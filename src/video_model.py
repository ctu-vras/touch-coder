import sys
from typing import Dict

from domain.model import FrameBundle, LimbView

PROGRAM_VERSION = "8.0.0"


class Video:
    """Per-video state container. Does NO I/O: total_frames comes from the
    caller (probe via adapters.video_probe), frame_rate is set after probing.
    """

    def __init__(self, video_path, total_frames):
        self.video_path = video_path
        self.current_frame = 0  # Starting at frame 0
        self.current_frame_zone = 0
        self.number_frames_in_zone = 100
        self.video_name = None
        self.total_frames = total_frames
        self.number_zones = int(self.total_frames/self.number_frames_in_zone) + 1
        self.frames_dir = None
        self.data_path_to_csv = None
        self.dots = []
        self.frames: Dict[int, FrameBundle] = {}
        # Expose limb views so existing code (dataRH/LH/RL/LL) keeps working:
        self.dataRH = LimbView(self, "RH")
        self.dataLH = LimbView(self, "LH")
        self.dataRL = LimbView(self, "RL")
        self.dataLL = LimbView(self, "LL")

        self.is_touchRH = False
        self.is_touchLH = False
        self.is_touchRL = False
        self.is_touchLL = False
        self.touch_to_next_zone = [False for _ in range(self.number_zones)]
        self.last_green = [(10, 10),(5, 5),(50, 50)]
        self.play = False
        self.frame_rate = None

        if sys.platform.startswith("win"):
            self.program_version = f"{PROGRAM_VERSION} (Windows)"
        elif sys.platform.startswith("linux"):
            self.program_version = f"{PROGRAM_VERSION} (Linux)"
        else:
            self.program_version = f"{PROGRAM_VERSION} (Unknown OS)"
        print("INFO: Program version:", self.program_version)
        self.parameter1_name = None
        self.parameter2_name = None
        self.parameter3_name = None
        # Display-only note fallback for pre-unified projects, loaded from the
        # state DB's `legacy_notes` table (never part of bundle["Note"]).
        self.notes = {}
