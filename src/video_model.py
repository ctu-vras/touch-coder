import cv2


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
        self.dataRH = {}
        self.dataRH_path_to_csv = None
        self.dataLH = {}
        self.dataLH_path_to_csv = None
        self.dataRL = {}
        self.dataRL_path_to_csv = None
        self.dataLL = {}
        self.dataLL_path_to_csv = None

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
        self.program_version = 6.0
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
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
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
