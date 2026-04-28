import os
import sys
import csv
import json
import time
import shutil
import re
import traceback
from threading import Thread, Event, RLock
from concurrent.futures import ThreadPoolExecutor

import cv2
import pandas as pd
import keyboard
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk, ImageDraw

import analysis
from cloth_app import ClothApp, DEFAULT_CLOTH_DIAGRAM_SCALE
from config_utils import (
    load_config,
    save_config,
    load_config_flags,
    load_parameter_names_into,
    load_perf_config,
    load_video_downscale,
    load_jump_seconds,
)
from data_utils import (
    bundle_summary_str,
    csv_to_dict, save_dataset, save_parameter_to_csv, load_parameter_from_csv,
    save_limb_parameters, load_limb_parameters, merge_and_flip_export, extract_zones_from_file,
    FrameRecord,
)
from frame_utils import check_items_count, create_frames
from perf_utils import PerfLogger
from pose_mismatch_data import (
    POSE_JOINTS,
    empty_pose_bundle,
    ensure_pose_bundle,
    export_pose_dataset,
    load_pose_dataset,
    save_pose_dataset,
    scale_raw_to_factor,
)
from resource_utils import resource_path
from sort_frames import process_touch_data_strict_transitions
from ui_components import build_ui
from video_model import Video


# =============================================================================
# Constants
# =============================================================================
PLAYBACK_BUFFER_PAUSE_S = 1.0
PLAYBACK_BUFFER_AHEAD = 3
BUFFER_MAX_BYTES = 1_000_000_000
DEBUG = False
THREE_D_MODE = "3D Mismatch"
POSE_OUTLINE_ANCHOR_X = 183.0
POSE_OUTLINE_ANCHOR_Y = 348.0
POSE_OUTLINE_ALPHA = 90


# =============================================================================
# Standalone helpers
# =============================================================================
def custom_confirm_close(root, saved: bool):
    win = tk.Toplevel(root)
    win.title("Close Application")
    win.geometry("600x300")
    win.resizable(True, True)
    win.grab_set()  # makes it modal

    msg = tk.Label(
        win,
        text="Do you want to close the application?\n\nProgress was saved." if saved else "Do you want to close the application?\n\n",
        font=("Segoe UI", 11),
        justify="center",
        wraplength=350
    )
    msg.pack(expand=True, fill="both", padx=20, pady=20)

    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=10)

    def on_yes():
        
        win.destroy()
        root.destroy()

    ttk.Button(btn_frame, text="OK", command=on_yes).pack(side="left", padx=10)
    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="left", padx=10)


# =============================================================================
# Main Application
# =============================================================================
class LabelingApp(tk.Tk):
    def __init__(self):
        super().__init__()

        # Core state (was previously in __init__)
        self.video = None
        self.video_name = None
        self.minimal_touch_length = None
        self.NEW_TEMPLATE = False
        self.annotation_mode = "touch"
        self.clothes_diagram_scale = DEFAULT_CLOTH_DIAGRAM_SCALE
        self._cloth_app = None
        self._video_time_total_s = 0.0
        self._video_session_start = None
        self._zone_masks = []
        self._zone_centroids = {}
        self._zone_dir = None
        self._pose_timeline_state_cache = None
        self._base_diagram_image = None
        self._outline_image = None
        self._pose_canvas_dirty = False
        self.current_pose_scale = 1.0
        self._last_pose_render_signature = None
        self._pose_timeline2_photo = None
        self._pose_timeline2_image_id = None
        self._updating_scale_widget = False
        self._scale_drag_active = False
        self._pose_scale_carry_active = False
        self._last_displayed_frame = None

        # Build UI (creates frames, widgets, binds events; sets many attributes)
        build_ui(self)

        # Load config flags that affect UI sizing & behavior
        self.NEW_TEMPLATE, self.minimal_touch_length = load_config_flags()
        print("INFO: Loaded new template:", self.NEW_TEMPLATE)
        print("INFO: Loaded minimal touch length:", self.minimal_touch_length)
        perf_enabled, perf_log_every_s, perf_log_top_n = load_perf_config()
        self.perf = PerfLogger(
            enabled=perf_enabled,
            log_every_s=perf_log_every_s,
            top_n=perf_log_top_n,
        )
        print("INFO: Perf logging enabled:", perf_enabled)
        self.video_downscale = load_video_downscale()
        print("INFO: Video downscale:", self.video_downscale)
        self.jump_seconds = load_jump_seconds()
        self.jump_frame_count = 7  # fallback until a video loads & framerate is known
        print(f"INFO: Fast-jump configured to {self.jump_seconds}s")
        self._refresh_jump_label()

        # Timeline and buffering helpers
        self.background_thread = Thread(target=self.background_update, daemon=True)
        self.background_thread_play = Thread(target=self.background_update_play, daemon=True)
        self.buffer_ready = False

        # Priority-load + parallel-prefetch infrastructure
        self._priority_frame = None              # frame the user explicitly wants ASAP
        self._priority_event = Event()           # wakes background_update on jump
        self._buffer_lock = RLock()              # serializes img_buffer mutations
        self._loader_pool = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="frame-loader"
        )
        self._inflight_frames = set()            # frame indices currently being decoded
        self._buffer_gen = 0                     # bumps on _buffer_reset to discard stale workers
        self._last_step_sign = 0                 # +1 forward / -1 backward / 0 none

        # Diagram init
        self.init_diagram()
        # Timeline draw cache
        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._timeline_last_zone = None
        self._timeline_last_limb = None
        self._timeline2_last_limb = None
        self._timeline2_last_limb = None
        self._timeline_canvas_size = (0, 0)
        self._timeline2_canvas_size = (0, 0)
        self._timeline_playhead_id = None
        self._timeline2_playhead_id = None
        self._pose_timeline_scale_overlay_id = None
        self._pose_timeline2_scale_overlay_id = None
    # === 3D Pose Mode & Rendering ============================================
    def _limb_param_key_for_index(self, idx: int) -> str:
        return f"Par{idx}"

    def is_pose_mode(self) -> bool:
        return getattr(self, "annotation_mode", "touch") == "pose_3d"

    def _annotation_mode_suffix(self) -> str:
        return "_3d" if self.is_pose_mode() else ""

    def _selected_pose_joint_event_summary(self, frame: int | None = None) -> str:
        if not self.video:
            return "No joint events"
        if frame is None:
            frame = self.video.current_frame
        bundle = ensure_pose_bundle(self.video.frames.get(frame))
        parts = []
        for joint in POSE_JOINTS:
            event = bundle["Joints"].get(joint, {}).get("Event")
            if event:
                parts.append(f"{joint}:{event}")
        return ", ".join(parts) if parts else "No joint events"

    def _get_effective_pose_scale(self, frame: int | None = None) -> tuple[float, float]:
        with self.perf.time("pose_get_effective_scale"):
            if frame is None:
                frame = self.video.current_frame if self.video else 0
            if not self.video:
                return 1.0, 1.0
            bundle = ensure_pose_bundle(self.video.frames.get(frame))
            if bundle.get("ScaleSet"):
                raw = float(bundle.get("ScaleRaw", 1.0) or 1.0)
                factor = float(bundle.get("ScaleFactor", scale_raw_to_factor(raw)) or 1.0)
                return raw, factor
            if frame == self.video.current_frame:
                current = float(getattr(self, "current_pose_scale", 1.0) or 1.0)
                return current, current
            return 1.0, 1.0

    def _log_pose_scale(self, message: str):
        print(f"INFO: 3D scale {message}")

    def _set_pose_scale_for_frame(self, frame: int, raw: float, redraw_overview: bool, auto_carried: bool) -> bool:
        if not self.video:
            return False
        bundle = self._ensure_bundle(frame)
        raw = float(raw)
        factor = scale_raw_to_factor(raw)
        if (
            bundle.get("ScaleSet")
            and abs(float(bundle.get("ScaleRaw", 1.0) or 1.0) - raw) < 1e-9
            and abs(float(bundle.get("ScaleFactor", 1.0) or 1.0) - factor) < 1e-9
            and bool(bundle.get("ScaleAutoCarry", False)) == bool(auto_carried)
        ):
            return False
        bundle["ScaleRaw"] = raw
        bundle["ScaleFactor"] = factor
        bundle["ScaleSet"] = True
        bundle["ScaleAutoCarry"] = bool(auto_carried)
        bundle["Changed"] = True
        if self._pose_timeline_state_cache is not None:
            cached = self._pose_timeline_state_cache.get(frame)
            if isinstance(cached, dict):
                cached["scale_raw"] = raw
                cached["scale_factor"] = factor
        self._timeline_dirty = True
        if redraw_overview:
            self._timeline2_dirty = True
        source = "auto-carry" if auto_carried else "manual"
        self._log_pose_scale(f"write frame={frame} scale={raw:.2f} source={source} redraw_overview={redraw_overview}")
        return True

    def _remove_white_background(self, image: Image.Image, threshold: int = 245) -> Image.Image:
        image = image.convert("RGBA")
        px = image.load()
        width, height = image.size
        for y in range(height):
            for x in range(width):
                r, g, b, a = px[x, y]
                if r >= threshold and g >= threshold and b >= threshold:
                    px[x, y] = (r, g, b, 0)
        return image

    def _apply_outline_alpha(self, image: Image.Image, alpha: int) -> Image.Image:
        image = image.convert("RGBA")
        px = image.load()
        width, height = image.size
        target_alpha = max(0, min(255, int(alpha)))
        for y in range(height):
            for x in range(width):
                r, g, b, a = px[x, y]
                if a > 0:
                    px[x, y] = (r, g, b, min(a, target_alpha))
        return image

    def render_pose_canvas(self):
            with self.perf.time("pose_render_canvas"):
                if not self.is_pose_mode():
                    return
            if self._base_diagram_image is None:
                with self.perf.time("pose_render_load_base"):
                    self._base_diagram_image = Image.open(resource_path("icons/3d/diagram.png")).convert("RGBA")
            if self._outline_image is None:
                with self.perf.time("pose_render_load_outline"):
                    outline = self._remove_white_background(Image.open(resource_path("icons/3d/outline.png")))
                    self._outline_image = self._apply_outline_alpha(outline, POSE_OUTLINE_ALPHA)

            base_img = self._base_diagram_image
            outline_img = self._outline_image
            scale = getattr(self, "diagram_scale", 1.0)
            canvas_w = int(base_img.width * scale)
            canvas_h = int(base_img.height * scale)
            self.diagram_canvas.config(width=canvas_w, height=canvas_h)
            dots_signature = []
            if self.video:
                bundle = ensure_pose_bundle(self.video.frames.get(self.video.current_frame))
                joints = bundle.get("Joints") or {}
                for joint in POSE_JOINTS:
                    rec = joints.get(joint, {})
                    event = rec.get("Event")
                    x = rec.get("X")
                    y = rec.get("Y")
                    if event and x is not None and y is not None:
                        dots_signature.append((joint, event, int(x), int(y)))
            _raw, factor = self._get_effective_pose_scale(self.video.current_frame if self.video else 0)
            signature = (round(scale, 4), round(factor, 4), tuple(dots_signature))
            if (not self._pose_canvas_dirty) and self._last_pose_render_signature == signature:
                return

            with self.perf.time("pose_render_compose"):
                composed = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
                base_resized = base_img.resize((canvas_w, canvas_h), Image.LANCZOS)
                composed.paste(base_resized, (0, 0), base_resized)

                outline_w = max(1, int(canvas_w * factor))
                outline_h = max(1, int(canvas_h * factor))
                outline_resized = outline_img.resize((outline_w, outline_h), Image.LANCZOS)
                anchor_x = POSE_OUTLINE_ANCHOR_X * scale
                anchor_y = POSE_OUTLINE_ANCHOR_Y * scale
                overlay_x = int(round(anchor_x - (anchor_x * factor)))
                overlay_y = int(round(anchor_y - (anchor_y * factor)))
                composed.paste(outline_resized, (overlay_x, overlay_y), outline_resized)

            with self.perf.time("pose_render_canvas_draw"):
                self.photo = ImageTk.PhotoImage(composed)
                self.diagram_canvas.delete("all")
                self.diagram_canvas.create_image(0, 0, anchor="nw", image=self.photo)

                dot_size = getattr(self, "dot_size", 10)
                if self.video:
                    for joint in POSE_JOINTS:
                        rec = joints.get(joint, {})
                        x = rec.get("X")
                        y = rec.get("Y")
                        event = rec.get("Event")
                        if x is None or y is None or not event:
                            continue
                        color = "green" if event == "ON" else "red"
                        self.diagram_canvas.create_oval(
                            x * scale - dot_size,
                            y * scale - dot_size,
                            x * scale + dot_size,
                            y * scale + dot_size,
                            fill=color,
                            outline=color,
                        )

            self._pose_canvas_dirty = False
            self._last_pose_render_signature = signature

    def _find_nearest_pose_joint(self, x: float, y: float, max_distance: float = 28.0):
        if not getattr(self, "_zone_centroids", None):
            return None
        best_joint = None
        best_d2 = None
        for joint in POSE_JOINTS:
            center = self._zone_centroids.get(joint)
            if not center:
                continue
            cx, cy = center
            d2 = (cx - x) * (cx - x) + (cy - y) * (cy - y)
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best_joint = joint
        if best_joint is None or best_d2 is None:
            return None
        if best_d2 <= max_distance * max_distance:
            return best_joint
        return None

    def _pose_joint_distances(self, x: float, y: float, limit: int = 5):
        distances = []
        for joint in POSE_JOINTS:
            center = self._zone_centroids.get(joint)
            if not center:
                continue
            cx, cy = center
            d2 = (cx - x) * (cx - x) + (cy - y) * (cy - y)
            distances.append((joint, d2 ** 0.5, center))
        distances.sort(key=lambda item: item[1])
        return distances[:limit]

    def _probe_pose_zone_neighbors(self, x: float, y: float, radius: int = 10, step: int = 2):
        if not getattr(self, "_zone_masks", None):
            return []
        found = {}
        x0 = int(x)
        y0 = int(y)
        for dy in range(-radius, radius + 1, step):
            for dx in range(-radius, radius + 1, step):
                px = x0 + dx
                py = y0 + dy
                for zone_name, image in self._zone_masks:
                    if zone_name not in POSE_JOINTS:
                        continue
                    h, w = image.shape[:2]
                    if px < 0 or py < 0 or px >= w or py >= h:
                        continue
                    if image[py, px] == 0:
                        dist = (dx * dx + dy * dy) ** 0.5
                        prev = found.get(zone_name)
                        if prev is None or dist < prev[0]:
                            found[zone_name] = (dist, px, py)
        items = sorted(found.items(), key=lambda item: item[1][0])
        return items[:5]

    def _set_sort_analysis_state(self):
        is_pose = self.is_pose_mode()
        has_video = self.video is not None
        sort_state = tk.NORMAL if (has_video and not is_pose) else tk.DISABLED
        analysis_state = tk.NORMAL if (has_video and not is_pose) else tk.DISABLED
        cloth_state = tk.NORMAL if (has_video and not is_pose) else tk.DISABLED
        if getattr(self, "sort_btn", None):
            self.sort_btn.config(state=sort_state)
        if getattr(self, "analysis_btn", None):
            self.analysis_btn.config(state=analysis_state)
        if getattr(self, "cloth_btn", None):
            self.cloth_btn.config(state=cloth_state)

    # === UI Rebuild & Annotation Controls =====================================
    def _reset_zone_cache(self):
        self._zone_masks = []
        self._zone_centroids = {}
        self._zone_dir = None
        self._pose_timeline_state_cache = None
        self._last_pose_render_signature = None

    def _clear_frame_children(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def rebuild_annotation_controls(self):
        if not hasattr(self, "mode_controls_frame"):
            return

        self._clear_frame_children(self.mode_controls_frame)
        self._clear_frame_children(self.limb_parameter_frame)
        self.limb_par1_btn = None
        self.limb_par2_btn = None
        self.limb_par3_btn = None
        self.scale_var = getattr(self, "scale_var", tk.DoubleVar(value=1.0))

        if self.is_pose_mode():
            if getattr(self, "mode_param_label", None):
                self.mode_param_label.config(text="Scale")
            if getattr(self, "mode_param_subtitle", None):
                self.mode_param_subtitle.config(text="(3D body mismatch)")
            tk.Label(
                self.mode_controls_frame,
                text="3D Mismatch",
                font=("Arial", 10, "bold"),
                bg="lightgrey",
            ).pack(anchor="n", pady=(5, 2))
            tk.Label(
                self.mode_controls_frame,
                text="Click a joint zone for onset/offset",
                bg="lightgrey",
            ).pack(anchor="n")

            tk.Label(
                self.limb_parameter_frame,
                text="Body Scale",
                font=("Arial", 10, "bold"),
                bg="lightgrey",
            ).pack(anchor="n", pady=(5, 2))

            self.scale_value_label = tk.Label(
                self.limb_parameter_frame,
                text="Scale: 1.00x",
                bg="lightgrey",
            )
            self.scale_value_label.pack(anchor="n")

            scale_controls = tk.Frame(self.limb_parameter_frame, bg="lightgrey")
            scale_controls.pack(anchor="n", pady=(2, 6))

            self.scale_widget = tk.Scale(
                scale_controls,
                from_=0.7,
                to=1.3,
                resolution=0.01,
                orient=tk.HORIZONTAL,
                length=180,
                variable=self.scale_var,
                command=self.on_scale_changed,
                bg="lightgrey",
                highlightthickness=0,
                takefocus=0,
            )
            self.scale_widget.pack(side="left", anchor="n")
            self.scale_widget.bind("<Button-1>", self._on_scale_press)
            self.scale_widget.bind("<ButtonRelease-1>", self._on_scale_release)
            self.scale_widget.bind("<Key>", lambda _event: "break")
            self.scale_widget.bind("<MouseWheel>", lambda _event: "break")
            self.scale_widget.bind("<Button-4>", lambda _event: "break")
            self.scale_widget.bind("<Button-5>", lambda _event: "break")

            tk.Button(
                scale_controls,
                text="1.00",
                command=self.reset_pose_scale,
                width=5,
                height=1,
            ).pack(side="left", padx=(6, 0))

            self.pose_events_label = tk.Label(
                self.limb_parameter_frame,
                text="No joint events",
                bg="lightgrey",
                justify="left",
                wraplength=220,
            )
            self.pose_events_label.pack(anchor="n")
        else:
            if getattr(self, "mode_param_label", None):
                self.mode_param_label.config(text="Parameters")
            if getattr(self, "mode_param_subtitle", None):
                self.mode_param_subtitle.config(text="(Limb-Specific)")
            tk.Label(
                self.mode_controls_frame,
                text="Limb Selector",
                font=("Arial", 10, "bold"),
                bg="lightgrey",
            ).pack(anchor="n", pady=(5, 2))

            for text, value in (
                ("Right Hand", "RH"),
                ("Left Hand", "LH"),
                ("Right Leg", "RL"),
                ("Left Leg", "LL"),
            ):
                tk.Radiobutton(
                    self.mode_controls_frame,
                    text=text,
                    variable=self.option_var_1,
                    value=value,
                    bg="lightgrey",
                    command=self.on_radio_click,
                ).pack(anchor="n")

            self.limb_par1_btn = tk.Button(
                self.limb_parameter_frame,
                text="Limb Parameter 1",
                command=lambda: self.toggle_limb_parameter(1),
                width=15,
                height=1,
            )
            self.limb_par1_btn.pack(anchor="n")
            self.limb_par2_btn = tk.Button(
                self.limb_parameter_frame,
                text="Limb Parameter 2",
                command=lambda: self.toggle_limb_parameter(2),
                width=15,
                height=1,
            )
            self.limb_par2_btn.pack(anchor="n")
            self.limb_par3_btn = tk.Button(
                self.limb_parameter_frame,
                text="Limb Parameter 3",
                command=lambda: self.toggle_limb_parameter(3),
                width=15,
                height=1,
            )
            self.limb_par3_btn.pack(anchor="n")

        self._set_sort_analysis_state()

    # === Pose Scale Controls ==================================================
    def on_scale_changed(self, _value=None):
        if self._updating_scale_widget or not self.video or not self.is_pose_mode():
            return
        if not self._scale_drag_active:
            return
        frame = self.video.current_frame
        raw = float(self.scale_var.get())
        self.current_pose_scale = raw
        self._pose_scale_carry_active = True
        self._set_pose_scale_for_frame(frame, raw, redraw_overview=True, auto_carried=False)
        self.update_pose_scale_label()
        self._pose_canvas_dirty = True
        self.render_pose_canvas()
        self.draw_timeline()
        self.draw_timeline2()

    def reset_pose_scale(self):
        if not self.video or not self.is_pose_mode():
            return
        raw = 1.0
        self.current_pose_scale = raw
        self._pose_scale_carry_active = True
        self._set_pose_scale_for_frame(self.video.current_frame, raw, redraw_overview=True, auto_carried=False)
        self._updating_scale_widget = True
        try:
            if getattr(self, "scale_var", None) is not None:
                self.scale_var.set(raw)
        finally:
            self._updating_scale_widget = False
        self.update_pose_scale_label()
        self._pose_canvas_dirty = True
        self.render_pose_canvas()
        self.draw_timeline()
        self.draw_timeline2()

    def update_pose_scale_label(self):
        if not self.is_pose_mode():
            return
        factor = 1.0
        if self.video:
            _raw, factor = self._get_effective_pose_scale(self.video.current_frame)
        if getattr(self, "scale_value_label", None):
            self.scale_value_label.config(text=f"Scale: {factor:.2f}x")
        if getattr(self, "pose_events_label", None):
            self.pose_events_label.config(text=self._selected_pose_joint_event_summary())

    def _on_scale_press(self, event):
        try:
            hit = self.scale_widget.identify(event.x, event.y)
        except Exception:
            hit = None
        if hit != "slider":
            self._scale_drag_active = False
            return "break"
        self._scale_drag_active = True
        return None

    def _on_scale_release(self, _event):
        self._scale_drag_active = False

    # === Data Bundle Management ================================================
    def _ensure_limb_params(self, rec: dict) -> dict:
        if not isinstance(rec.get("LimbParams"), dict):
            rec["LimbParams"] = {}
        return rec["LimbParams"]
    
    def _param_next_state(self, current):
        # Cycle: None -> "ON" -> "OFF" -> "ON" ...
        if current is None or current == "":
            return "ON"
        if current == "ON":
            return "OFF"
        if current == "OFF":
            return None
        return None  # current == "ON" or anything else

    def _param_key_for_index(self, idx: int) -> str:
        return f"Par{idx}"
        
    def on_note_changed(self, text: str):
        idx = self.frame_index
        b = self._ensure_bundle(idx)
        if b.get("Note") != text:
            b["Note"] = text
            self.mark_bundle_changed(idx)
    
    def _ensure_bundle(self, idx: int):
        b = self.video.frames.get(idx)
        if self.is_pose_mode():
            b = ensure_pose_bundle(b)
            self.video.frames[idx] = b
            return b
        if not isinstance(b, dict):
            # create an empty bundle (match your empty_bundle() structure)
            b = {
                "Note": None,
                "Params": {},
                "LH": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "RH": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "LL": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                "RL": {"Onset": None, "Look": None, "Touch": None, "Zones": [], "X": [], "Y": []},
                # no "Changed" by default
            }
            self.video.frames[idx] = b
        return b

    def _ensure_params(self, b: dict):
        if "Params" not in b or not isinstance(b["Params"], dict):
            b["Params"] = {}
        return b["Params"]
    def mark_bundle_changed(self, index=None):
        if self.video is None:
            return
        idx = self.video.current_frame
        
        b = self.video.frames.get(idx)
        if isinstance(b, dict):
            b["Changed"] = True
            self._timeline_dirty = True
            self._timeline2_dirty = True
            self._pose_timeline_state_cache = None
            # optional: keep your terminal print
            if hasattr(self, "notify_bundle_changed"):
                self.notify_bundle_changed(idx)

    def notify_bundle_changed(self, index=None):
        if self.video is None:
            return
        idx = self.video.current_frame
        try:
            b = self.video.frames[idx]
            if DEBUG:
                print("\n=== FrameBundle UPDATED ===")
                print(bundle_summary_str(b, frame_index=idx))
        except Exception as e:
            print(f"[notify_bundle_changed] could not print bundle at {idx}: {e}")
    
    def _get_bundle(self, frame):
        if self.is_pose_mode():
            existing = self.video.frames.get(frame)
            bundle = ensure_pose_bundle(existing)
            self.video.frames[frame] = bundle
            return bundle
        from data_utils import empty_bundle
        return self.video.frames.setdefault(frame, empty_bundle())

    def set_param_on_frame(self, frame, name, state):  # state: "ON"/"OFF"/None
        b = self._get_bundle(frame)
        params = b.get("Params", {}) or {}
        params[name] = state
        b["Params"] = params
    
    # === Navigation & Input Events =============================================
    def global_click(self, event):
        try:
            focus = self.focus_get()
        except Exception:
            focus = None
        if getattr(self, "note_entry", None) and focus == self.note_entry and event.widget != self.note_entry:
            self.focus_set()

    def _set_loading_label_async(self, text: str, bg: str):
        current = getattr(self, "_loading_label_state", None)
        new_state = (text, bg)
        if current == new_state:
            return
        self._loading_label_state = new_state

        def _apply():
            if getattr(self, "loading_label", None):
                self.loading_label.config(text=text, bg=bg)

        self.after(0, _apply)

    def navigate_left(self, event): self._request_buffered_step(-1)
    
    def navigate_right(self, event): self._request_buffered_step(1)

    def disable_arrow_keys(self, event=None):
        self.unbind("<Left>")
        self.unbind("<Right>")
        self.unbind("<Shift-Left>")
        self.unbind("<Shift-Right>")

    def enable_arrow_keys(self, event=None):
        self.bind("<Left>", self.navigate_left)
        self.bind("<Right>", self.navigate_right)
        self.bind("<Shift-Left>", lambda event: self._request_buffered_step(-self.jump_frame_count))
        self.bind("<Shift-Right>", lambda event: self._request_buffered_step(self.jump_frame_count))

    def _refresh_jump_label(self):
        label = getattr(self, "jump_label", None)
        if label is None:
            return
        if self.video is not None and getattr(self, "frame_rate", None):
            text = f"Jump: {self.jump_frame_count} frames ({self.jump_seconds}s)"
        else:
            text = f"Jump: {self.jump_seconds}s (load video to see frames)"
        label.config(text=text)

    def update_last_mouse_position(self, event):
        self.last_mouse_x = event.x
        self.last_mouse_y = event.y

    def on_resize(self, event):
        print("INFO: Resized to {}x{}".format(event.width, event.height))
        self._buffer_reset()
        if self.video:
            self.display_first_frame()

    def on_mouse_wheel(self, event):
        if event.delta > 0 or getattr(event, "num", None) == 4:
            self._request_buffered_step(-1)
        elif event.delta < 0 or getattr(event, "num", None) == 5:
            self._request_buffered_step(1)

    def _request_buffered_step(self, delta):
        if self.video is None:
            return
        self._pending_buffer_step = delta
        # Hint the buffering thread to prioritize the jump target so the polling
        # loop in _buffered_step_tick picks it up on the next 50ms tick.
        target = max(0, min(self.video.total_frames, self.video.current_frame + delta))
        if target not in self.img_buffer:
            self._priority_frame = target
            self._priority_event.set()
        self._buffered_step_tick()

    def _buffered_step_tick(self):
        if self.video is None:
            self._pending_buffer_step = None
            return
        delta = getattr(self, "_pending_buffer_step", None)
        if delta is None:
            return
        current_frame = self.video.current_frame
        next_frame = max(0, min(self.video.total_frames, current_frame + delta))
        if current_frame not in self.img_buffer or next_frame not in self.img_buffer:
            self.buffer_ready = False
            self.after(50, self._buffered_step_tick)
            return
        self._pending_buffer_step = None
        self.next_frame(delta)

    def on_middle_click(self, event=None):
        if self.is_pose_mode():
            if self.video is None:
                return
            if event is None or isinstance(event, tk.Event):
                x_disp, y_disp = self.last_mouse_x, self.last_mouse_y
            else:
                x_disp, y_disp = event.x, event.y

            scale = getattr(self, "diagram_scale", 1.0)
            x_pos = x_disp * (1.0 / scale)
            y_pos = y_disp * (1.0 / scale)
            bundle = self._ensure_bundle(self.video.current_frame)
            joints = bundle.get("Joints") or {}

            closest_joint = None
            closest_d2 = None
            for joint in POSE_JOINTS:
                rec = joints.get(joint, {})
                x = rec.get("X")
                y = rec.get("Y")
                event_state = rec.get("Event")
                if x is None or y is None or not event_state:
                    continue
                d2 = (x - x_pos) * (x - x_pos) + (y - y_pos) * (y - y_pos)
                if closest_d2 is None or d2 < closest_d2:
                    closest_d2 = d2
                    closest_joint = joint

            if closest_joint is None or closest_d2 is None:
                print(f"INFO: 3D middle click found no removable dot near ({int(x_pos)}, {int(y_pos)})")
                return

            if closest_d2 <= (20.0 / scale) ** 2:
                joints[closest_joint]["Event"] = None
                joints[closest_joint]["X"] = None
                joints[closest_joint]["Y"] = None
                self.mark_bundle_changed(self.video.current_frame)
                self._pose_canvas_dirty = True
                self.render_pose_canvas()
                self.update_pose_scale_label()
                self.draw_timeline()
                self.draw_timeline2()
                print(f"INFO: Removed 3D dot for {closest_joint} on frame {self.video.current_frame}")
            else:
                print(
                    f"INFO: 3D middle click nearest dot too far at ({int(x_pos)}, {int(y_pos)}), "
                    f"nearest={closest_joint}, distance={closest_d2 ** 0.5:.1f}"
                )
            return
        # mouse position in display coords; convert to data coords using diagram_scale
        if event is None or isinstance(event, tk.Event):
            x_disp, y_disp = self.last_mouse_x, self.last_mouse_y
        else:
            x_disp, y_disp = event.x, event.y

        scale = getattr(self, "diagram_scale", 1.0)
        x_pos = x_disp * (1.0 / scale)
        y_pos = y_disp * (1.0 / scale)

        current_frame = self.video.current_frame
        option = self.option_var_1.get()

        target_attr = f"data{option}" if hasattr(self.video, f"data{option}") else "data"
        target_data = getattr(self.video, target_attr, {})

        rec = target_data.get(current_frame)
        if not isinstance(rec, dict):
            return  # nothing to delete

        xs = rec.get('X', [])
        ys = rec.get('Y', [])
        zones = rec.get('Zones', [])

        # normalize Zones to list-of-lists (to align with X/Y)
        if zones and isinstance(zones[0], (int, str)):
            zones = [[z] for z in zones]
            rec['Zones'] = zones

        if not xs or not ys:
            return

        # find closest point (euclidean in data coords)
        closest_idx = None
        closest_d2 = float('inf')
        for i, (x, y) in enumerate(zip(xs, ys)):
            d2 = (x - x_pos) * (x - x_pos) + (y - y_pos) * (y - y_pos)
            if d2 < closest_d2:
                closest_d2 = d2
                closest_idx = i

        # threshold in data coords (≈20 px in display); translates to 20/scale
        if closest_idx is not None and closest_d2 <= (20.0 / scale) ** 2:
            # delete this point and its zones bucket (if present)
            del xs[closest_idx]
            del ys[closest_idx]
            if isinstance(zones, list) and closest_idx < len(zones):
                del zones[closest_idx]

            if not xs:  # no points left -> clear the record to prevent export leakage
                target_data[current_frame] = {
                    "X": [],
                    "Y": [],
                    "Onset": "",          # important: clear onset
                    "Bodypart": option,   # keep limb name for consistency if needed
                    "Look": "No",
                    "Zones": [],
                    "Touch": None,
                }
            else:
                rec['X'] = xs
                rec['Y'] = ys
                rec['Zones'] = zones
                rec['Look'] = "No"  # or keep existing
                # keep Onset as-is for remaining points; you can also coerce if you prefer:
                # rec['Onset'] = "ON" if any remaining were added with ON else ""

            self.mark_bundle_changed()
            # Repaint immediately so the erased dot disappears without waiting
            # for the 300ms periodic_print_dot tick.
            self._render_diagram_dots()

    # === Diagram Init & Click Handling =========================================
    def init_diagram(self):
        # set up periodic dots refresh
        self._reset_zone_cache()
        self._load_zone_masks()
        self.periodic_print_dot()

    def _render_diagram_dots(self):
        """Repaint the diagram canvas with the current frame's dots + ghost.
        Single render pass — no scheduling. Safe to call from click handlers
        for instant visual feedback, and from the periodic poller as a fallback.
        """
        if self.is_pose_mode():
            if self._pose_canvas_dirty:
                self.render_pose_canvas()
                self.update_pose_scale_label()
            return
        self.diagram_canvas.delete("all")
        self.on_radio_click()  # keeps same behavior for image & palette
        dot_size = getattr(self, "dot_size", 10)
        scale = getattr(self, "diagram_scale", 1.0)
        if self.video and hasattr(self.video, 'data'):
            sel = self.option_var_1.get()
            if sel == "RH":
                data = self.video.dataRH
            elif sel == "LH":
                data = self.video.dataLH
            elif sel == "RL":
                data = self.video.dataRL
            elif sel == "LL":
                data = self.video.dataLL
            else:
                data = {}
            self.find_last_green(data)
            frame_data: FrameRecord | dict = data.get(self.video.current_frame, {})
            xs = frame_data.get('X', []) if frame_data else []
            ys = frame_data.get('Y', []) if frame_data else []
            onset = frame_data.get('Onset', "OFF") if frame_data else "OFF"
            for x, y in zip(xs, ys):
                color = 'green' if onset == "ON" else 'red'
                self.diagram_canvas.create_oval(
                    x * scale - dot_size, y * scale - dot_size,
                    x * scale + dot_size, y * scale + dot_size,
                    fill=color,
                )
            array_xy = getattr(self.video, "last_green", [(None, None)])
            for (x_last, y_last) in array_xy:
                if x_last is not None:
                    self.diagram_canvas.create_oval(
                        x_last * scale - dot_size, y_last * scale - dot_size,
                        x_last * scale + dot_size, y_last * scale + dot_size,
                        outline='green', fill='',
                    )

    def periodic_print_dot(self):
        if self.is_pose_mode():
            self._render_diagram_dots()
            self.after(1000, self.periodic_print_dot)
            return
        self._render_diagram_dots()
        self.after(300, self.periodic_print_dot)

    def on_diagram_click(self, event, is_onset):
        if self.video is None:
            return
        if self.is_pose_mode():
            with self.perf.time("pose_click_total"):
                onset = "ON" if is_onset else "OFF"
                display_scale = getattr(self, "diagram_scale", 1.0)
                x_pos = event.x * (1.0 / display_scale)
                y_pos = event.y * (1.0 / display_scale)
                zone_results = list(self.find_image_with_white_pixel(x_pos, y_pos))
                nearest = self._pose_joint_distances(x_pos, y_pos, limit=5)
                joint = next((zone for zone in zone_results if zone in POSE_JOINTS), None)
                if not joint:
                    joint = self._find_nearest_pose_joint(x_pos, y_pos)
                print(
                    "INFO: 3D click "
                    f"button={'left/onset' if is_onset else 'right/offset'} "
                    f"canvas=({event.x}, {event.y}) "
                    f"data=({int(x_pos)}, {int(y_pos)}) "
                    f"direct_hits={zone_results} "
                    f"nearest={[(name, round(dist, 1), (round(center[0],1), round(center[1],1))) for name, dist, center in nearest]} "
                    f"chosen={joint}"
                )
                if not joint:
                    neighbors = self._probe_pose_zone_neighbors(x_pos, y_pos, radius=16, step=2)
                    print(
                        "INFO: 3D click missed all joint zones "
                        f"at ({int(x_pos)}, {int(y_pos)}) "
                        f"neighbor_probe={[(name, round(dist,1), px, py) for name, (dist, px, py) in neighbors]}"
                    )
                    return
                bundle = self._ensure_bundle(self.video.current_frame)
                bundle["Joints"][joint]["Event"] = onset
                bundle["Joints"][joint]["X"] = int(x_pos)
                bundle["Joints"][joint]["Y"] = int(y_pos)
                self.mark_bundle_changed(self.video.current_frame)
                self.update_pose_scale_label()
                self._pose_canvas_dirty = True
                self.render_pose_canvas()
                self.draw_timeline()
                self.draw_timeline2()
                return
        onset = "ON" if is_onset else "OFF"
        display_scale = getattr(self, "diagram_scale", 1.0)
        x_pos = event.x * (1.0 / display_scale)
        y_pos = event.y * (1.0 / display_scale)
        zone_results = list(self.find_image_with_white_pixel(x_pos, y_pos))  # list for stability

        current_frame = self.video.current_frame
        option = self.option_var_1.get()

        target_attr = f"data{option}" if hasattr(self.video, f"data{option}") else "data"
        target_data = getattr(self.video, target_attr, {})
        setattr(self.video, f"is_touch{option}", True)

        print(f"CLICK: before  frame={current_frame:>5} limb={option} onset={onset} zones={zone_results}")

        existing = target_data.get(current_frame)
        if not isinstance(existing, dict) or (not existing.get('X') and not existing.get('Y')):
            rec = {
                "X": [int(x_pos)],
                "Y": [int(y_pos)],
                "Onset": onset,
                "Bodypart": option,
                "Look": "No",
                # IMPORTANT: store zones per point (list-of-lists)
                "Zones": [zone_results],   # <- one entry per point
                "Touch": None,
            }
            target_data[current_frame] = rec
        else:
            rec = existing
            rec.setdefault('X', []).append(int(x_pos))
            rec.setdefault('Y', []).append(int(y_pos))

            # normalize Zones to list-of-lists if older shape is present
            zones = rec.get('Zones', [])
            if zones and zones and isinstance(zones[0], (int, str)):
                # legacy shape -> convert to list-of-lists pairing length with X
                zones = [[z] for z in zones]
            rec['Zones'] = zones
            zones.append(zone_results)  # one zones bucket per point

            rec['Bodypart'] = option
            rec['Onset'] = onset
            rec['Look'] = "No"

        self.mark_bundle_changed()
        # Repaint immediately so the dot appears without waiting for the
        # 300ms periodic_print_dot tick.
        self._render_diagram_dots()

        rec = target_data.get(current_frame, {})
        print(
            f"CLICK:  after  frame={current_frame:>5} limb={option} onset={rec.get('Onset')} "
            f"points={len(rec.get('X', []))} zones_len={len(rec.get('Zones', []))}"
        )

    def preview_before_save(self, changed_only: bool = True):
        """
        Print a compact preview of what would be saved right now.
        Shows base/data/export dirs and per-frame summaries.
        """
        if not self.video:
            print("PREVIEW: No video loaded."); return


        base_dir = os.path.dirname(self.video.frames_dir)  # -> Labeled_data/<video>
        data_dir   = os.path.join(base_dir, "data")
        export_dir = os.path.join(base_dir, "export")
        unified_path = os.path.join(data_dir, f"{self.video_name}_unified.csv")
        export_path = os.path.join(export_dir, f"{self.video_name}_export.csv")

        print("\n===== PREVIEW: Save destinations =====")
        print(f"Base:   {base_dir}")
        print(f"Data:   {data_dir}")
        print(f"Export: {export_dir}")
        print(f"Unified CSV (will write changed-only): {unified_path}")
        print(f"Export  CSV (will write all frames):   {export_path}")

        if self.is_pose_mode():
            lines = []
            for frame in range(self.video.total_frames + 1):
                bundle = self.video.frames.get(frame)
                if not isinstance(bundle, dict):
                    continue
                if changed_only and not bundle.get("Changed"):
                    continue
                bundle = ensure_pose_bundle(bundle)
                _raw, factor = self._get_effective_pose_scale(frame)
                parts = [f"frame={frame:>5}", f"scale={factor:.2f}x"]
                summary = self._selected_pose_joint_event_summary(frame)
                if summary != "No joint events":
                    parts.append(summary)
                note = bundle.get("Note")
                if note:
                    parts.append(f'Note="{note}"')
                lines.append(" | ".join(parts))
        else:
            from data_utils import preview_lines_for_save
            lines = preview_lines_for_save(self.video.frames, self.video.total_frames, changed_only=changed_only)

        if not lines:
            print("PREVIEW: No changed frames to save.")
        else:
            print("===== PREVIEW: Frames to be saved =====")
            for line in lines:
                print(line)
            print("===== PREVIEW: End =====\n")
    
    def find_last_green(self, _unused_data=None):
        """
        Set self.video.last_green to the last 'ON' points for the selected limb
        at or before the current frame; clear when an 'OFF' is encountered first.

        Walks integer frame indices backward from current_frame instead of
        sorting all dict keys — O(distance to last ON/OFF) instead of
        O(N log N) per call. Matters at 300k+ frames.
        """
        if self.is_pose_mode():
            self.video.last_green = [(None, None)]
            return
        if not (self.video and isinstance(self.video.frames, dict)):
            self.video.last_green = [(None, None)]
            return

        limb = self.option_var_1.get()  # "LH"/"RH"/"LL"/"RL"
        frames = self.video.frames

        for f in range(self.video.current_frame, -1, -1):
            b = frames.get(f)
            if not isinstance(b, dict):
                continue
            rec = b.get(limb, {}) if isinstance(b, dict) else {}
            onset = rec.get("Onset")
            if onset == "OFF":
                self.video.last_green = [(None, None)]
                return
            if onset == "ON":
                xs = rec.get("X", []) or []
                ys = rec.get("Y", []) or []
                self.video.last_green = list(zip(xs, ys)) if xs and ys else [(None, None)]
                return

        self.video.last_green = [(None, None)]

    def on_radio_click(self):
        if self.is_pose_mode():
            expected_dir = resource_path("icons/3d/zones")
            if getattr(self, "_zone_dir", None) != expected_dir:
                self._reset_zone_cache()
                self._load_zone_masks()
            self.render_pose_canvas()
            self.update_pose_scale_label()
            self.draw_timeline()
            self.draw_timeline2()
            return
        expected_dir = resource_path("icons/zones3_new_template" if self.NEW_TEMPLATE else "icons/zones3")
        if getattr(self, "_zone_dir", None) != expected_dir:
            self._reset_zone_cache()
            self._load_zone_masks()
        if self.option_var_1.get() == "RH":
            image_path = resource_path("icons/RH_new_template.png" if self.NEW_TEMPLATE else "icons/RH.png")
        elif self.option_var_1.get() == "LH":
            image_path = resource_path("icons/LH_new_template.png" if self.NEW_TEMPLATE else "icons/LH.png")
        elif self.option_var_1.get() == "RL":
            image_path = resource_path("icons/RL_new_template.png" if self.NEW_TEMPLATE else "icons/RL.png")
        else:  # LL
            image_path = resource_path("icons/LL_new_template.png" if self.NEW_TEMPLATE else "icons/LL.png")

        img = Image.open(image_path)
        scale = getattr(self, "diagram_scale", 1.0)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(img)
        self.diagram_canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.draw_timeline()
        self.draw_timeline2()
        self.update_limb_parameter_buttons()

    # === Zone Masks & Lookups ==================================================
    def _load_zone_masks(self):
        if self.is_pose_mode():
            directory = resource_path("icons/3d/zones")
        else:
            directory = resource_path("icons/zones3_new_template" if self.NEW_TEMPLATE else "icons/zones3")
        if getattr(self, "_zone_dir", None) == directory and getattr(self, "_zone_masks", None):
            return
        self._zone_dir = directory
        self._zone_masks = []
        self._zone_centroids = {}
        if not os.path.isdir(directory):
            print(f"WARNING: Zones directory not found: {directory}")
            return
        for filename in os.listdir(directory):
            fp = os.path.join(directory, filename)
            if os.path.isfile(fp) and fp.lower().endswith(('.png', '.jpg', '.jpeg')):
                image = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
                if image is None:
                    continue
                zone_name = filename.rsplit('.', 1)[0]
                self._zone_masks.append((zone_name, image))
                if self.is_pose_mode() and zone_name in POSE_JOINTS:
                    ys, xs = (image == 0).nonzero()
                    if len(xs) > 0 and len(ys) > 0:
                        self._zone_centroids[zone_name] = (
                            float(xs.mean()),
                            float(ys.mean()),
                        )
        if self.is_pose_mode():
            print(
                f"INFO: Loaded 3D zone masks from {directory}: "
                f"{len(self._zone_masks)} masks, {len(self._zone_centroids)} joint centroids"
            )
        else:
            print(f"INFO: Loaded touch zone masks from {directory}: {len(self._zone_masks)} masks")

    def find_image_with_white_pixel(self, x, y):
        with self.perf.time("find_image_with_white_pixel"):
            x = int(x); y = int(y)
            if not getattr(self, "_zone_masks", None):
                self._load_zone_masks()
            matches = []
            for zone_name, image in self._zone_masks:
                h, w = image.shape[:2]
                if x < 0 or y < 0 or x >= w or y >= h:
                    continue
                if image[y, x] == 0:
                    matches.append(zone_name)
            if self.is_pose_mode():
                joint_matches = [zone for zone in matches if zone in POSE_JOINTS]
                if DEBUG:
                    print(
                        f"DEBUG: pose pixel probe at ({x}, {y}) "
                        f"matches={matches} joints={joint_matches} "
                        f"mask_count={len(self._zone_masks)}"
                    )
                if joint_matches:
                    return joint_matches
                return matches or ['NN']
            if matches:
                return [matches[0]]
            return ['NN']

    # === Timelines =============================================================
    def on_timeline_click(self, event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline_canvas.winfo_width()
            frame_number = int(click_position / canvas_width * self.video.number_frames_in_zone)
            if self.video.total_frames >= frame_number + self.video.number_frames_in_zone * self.video.current_frame_zone:
                self.video.current_frame = frame_number + self.video.number_frames_in_zone * self.video.current_frame_zone
                self.display_first_frame()
            else:
                print("ERROR: Frame Number")

    def on_timeline2_click(self, event):
        if self.video and self.video.total_frames > 0:
            click_position = event.x
            canvas_width = self.timeline2_canvas.winfo_width()
            new_frame = int((click_position / canvas_width) * self.video.total_frames)
            self.video.current_frame = new_frame
            self.video.current_frame_zone = new_frame // self.video.number_frames_in_zone
            print("INFO: Jumping to exact frame:", new_frame)
            self.display_first_frame()

    def parameter_color_at_frame(self, frame):
        b = self.video.frames.get(frame, {}) if self.video else {}
        params = (b.get("Params") or {})
        # If any param ON => green; else if any OFF => red; else None
        if any(v == "ON" for v in params.values()): return "green"
        if any(v == "OFF" for v in params.values()): return "red"
        return None

    def _pose_event_color_at_frame(self, frame):
        bundle = ensure_pose_bundle(self.video.frames.get(frame))
        joints = bundle.get("Joints") or {}
        events = [rec.get("Event") for rec in joints.values() if isinstance(rec, dict)]
        if any(event == "ON" for event in events):
            return "green"
        if any(event == "OFF" for event in events):
            return "#E57373"
        return None

    def _build_pose_timeline_state(self):
        with self.perf.time("pose_build_timeline_state"):
            if self._pose_timeline_state_cache is not None:
                return self._pose_timeline_state_cache
            active_joints = set()
            state = {}
            active_scale_raw = 1.0
            active_scale_factor = 1.0
            for frame in range(self.video.total_frames + 1):
                bundle = ensure_pose_bundle(self.video.frames.get(frame))
                joints = bundle.get("Joints") or {}
                events = {}
                for joint in POSE_JOINTS:
                    rec = joints.get(joint, {})
                    event = rec.get("Event") if isinstance(rec, dict) else None
                    if event == "ON":
                        active_joints.add(joint)
                        events[joint] = "ON"
                    elif event == "OFF":
                        active_joints.discard(joint)
                        events[joint] = "OFF"
                if bundle.get("ScaleSet"):
                    active_scale_raw = float(bundle.get("ScaleRaw", 1.0) or 1.0)
                    active_scale_factor = float(
                        bundle.get("ScaleFactor", scale_raw_to_factor(active_scale_raw)) or 1.0
                    )
                else:
                    active_scale_raw = 1.0
                    active_scale_factor = 1.0
                state[frame] = {
                    "events": events,
                    "active": set(active_joints),
                    "active_count": len(active_joints),
                    "scale_raw": active_scale_raw,
                    "scale_factor": active_scale_factor,
                }
            self._pose_timeline_state_cache = state
            return state

    def _draw_pose_timeline(self):
        canvas_width = self.timeline_canvas.winfo_width()
        canvas_height = self.timeline_canvas.winfo_height()
        zone = self.video.current_frame_zone
        needs_full = (
            self._timeline_dirty
            or self._timeline_last_zone != zone
            or self._timeline_last_limb != "POSE_3D"
            or self._timeline_canvas_size != (canvas_width, canvas_height)
        )
        sector_width = canvas_width / self.video.number_frames_in_zone if self.video.number_frames_in_zone else 1
        offset = self.video.number_frames_in_zone * zone
        top = 0
        bottom = canvas_height
        scale_min = 0.7
        scale_max = 1.3
        scale_top = 8
        scale_bottom = max(scale_top + 10, canvas_height - 16)
        if needs_full:
            self.timeline_canvas.delete("all")
            pose_state = self._build_pose_timeline_state()
            active_strip_h = 8

            for frame_offset in range(offset, min(offset + self.video.number_frames_in_zone, self.video.total_frames + 1)):
                left = (frame_offset - offset) * sector_width
                right = left + sector_width
                frame_state = pose_state.get(frame_offset, {})
                scale_factor = float(frame_state.get("scale_factor", 1.0) or 1.0)
                scale_ratio = (scale_factor - scale_min) / (scale_max - scale_min)
                scale_ratio = max(0.0, min(1.0, scale_ratio))
                y_scale = scale_bottom - (scale_bottom - scale_top) * scale_ratio
                self.timeline_canvas.create_rectangle(left, top, right, bottom, fill="#f1f1f1", outline="#d4d4d4")
                self.timeline_canvas.create_line(left + 1, y_scale, right - 1, y_scale, fill="#446a8a", width=2)

                active_count = int(frame_state.get("active_count", 0) or 0)
                if active_count > 0:
                    shade = max(180, 232 - (active_count * 8))
                    fill = f"#{shade:02x}{min(255, shade + 12):02x}{min(255, shade + 20):02x}"
                    self.timeline_canvas.create_rectangle(
                        left,
                        bottom - active_strip_h,
                        right,
                        bottom,
                        fill=fill,
                        outline="",
                    )

                mid_x = left + sector_width / 2
                on_count = sum(1 for ev in frame_state.get("events", {}).values() if ev == "ON")
                off_count = sum(1 for ev in frame_state.get("events", {}).values() if ev == "OFF")
                if on_count:
                    self.timeline_canvas.create_line(mid_x - 1, top + 2, mid_x - 1, bottom - active_strip_h - 2, fill="#2f8f57", width=1)
                if off_count:
                    self.timeline_canvas.create_line(mid_x + 1, top + 2, mid_x + 1, bottom - active_strip_h - 2, fill="#c56262", width=1)
                param_color = self.parameter_color_at_frame(frame_offset)
                if param_color:
                    self.timeline_canvas.create_line(mid_x + 3, top + 2, mid_x + 3, bottom - active_strip_h - 2, fill=param_color, width=2)

            self._timeline_dirty = False
            self._timeline_last_zone = zone
            self._timeline_last_limb = "POSE_3D"
            self._timeline_canvas_size = (canvas_width, canvas_height)
            self._timeline_playhead_id = None
            self._pose_timeline_scale_overlay_id = None

        current_pos = ((self.video.current_frame - offset) / self.video.number_frames_in_zone) * canvas_width
        left = max(0, min(canvas_width - 4, current_pos))
        if self._timeline_playhead_id is None:
            self._timeline_playhead_id = self.timeline_canvas.create_rectangle(left, top, left + 4, bottom, fill="dodgerblue", outline="")
        else:
            self.timeline_canvas.coords(self._timeline_playhead_id, left, top, left + 4, bottom)

        current_scale = float(self._get_effective_pose_scale(self.video.current_frame)[1] or 1.0)
        scale_ratio = (current_scale - scale_min) / (scale_max - scale_min)
        scale_ratio = max(0.0, min(1.0, scale_ratio))
        y_scale = scale_bottom - (scale_bottom - scale_top) * scale_ratio
        sector_left = max(0.0, min(canvas_width, (self.video.current_frame - offset) * sector_width))
        sector_right = max(sector_left + 1.0, min(canvas_width, sector_left + sector_width))
        if self._pose_timeline_scale_overlay_id is None:
            self._pose_timeline_scale_overlay_id = self.timeline_canvas.create_line(
                sector_left + 1,
                y_scale,
                sector_right - 1,
                y_scale,
                fill="#113a5c",
                width=3,
            )
        else:
            self.timeline_canvas.coords(
                self._pose_timeline_scale_overlay_id,
                sector_left + 1,
                y_scale,
                sector_right - 1,
                y_scale,
            )

    def _draw_pose_timeline2(self):
        canvas_width = self.timeline2_canvas.winfo_width()
        canvas_height = self.timeline2_canvas.winfo_height()
        needs_full = (
            self._timeline2_dirty
            or self._timeline2_last_limb != "POSE_3D"
            or self._timeline2_canvas_size != (canvas_width, canvas_height)
        )
        if needs_full:
            self.timeline2_canvas.delete("all")
            pose_state = self._build_pose_timeline_state()
            scale_min = 0.7
            scale_max = 1.3

            with self.perf.time("pose_draw_timeline2_raster"):
                img = Image.new("RGBA", (max(1, canvas_width), max(1, canvas_height)), (211, 211, 211, 255))
                draw = ImageDraw.Draw(img)
                total_frames = max(1, self.video.total_frames)

                for frame in range(self.video.total_frames + 1):
                    x = int(round((frame / total_frames) * (canvas_width - 1))) if canvas_width > 1 else 0
                    frame_state = pose_state.get(frame, {})
                    active_count = int(frame_state.get("active_count", 0) or 0)
                    if active_count > 0:
                        shade = max(185, 235 - (active_count * 8))
                        color = (shade, min(255, shade + 12), min(255, shade + 20), 255)
                        draw.line((x, canvas_height - 8, x, canvas_height), fill=color, width=2)

                    scale_factor = float(frame_state.get("scale_factor", 1.0) or 1.0)
                    scale_ratio = (scale_factor - scale_min) / (scale_max - scale_min)
                    scale_ratio = max(0.0, min(1.0, scale_ratio))
                    y_scale = int(round((canvas_height - 10) - ((canvas_height - 14) * scale_ratio)))
                    draw.point((x, y_scale), fill=(68, 106, 138, 255))

                    events = frame_state.get("events", {})
                    has_on = any(ev == "ON" for ev in events.values())
                    has_off = any(ev == "OFF" for ev in events.values())
                    if has_on and x - 1 >= 0:
                        draw.line((x - 1, 0, x - 1, canvas_height - 10), fill=(47, 143, 87, 255), width=1)
                    if has_off and x + 1 < canvas_width:
                        draw.line((x + 1, 0, x + 1, canvas_height - 10), fill=(197, 98, 98, 255), width=1)

                    param_color = self.parameter_color_at_frame(frame)
                    if param_color:
                        color_map = {
                            "green": (0, 128, 0, 255),
                            "#E57373": (229, 115, 115, 255),
                            "red": (255, 0, 0, 255),
                        }
                        rgba = color_map.get(param_color, (90, 90, 90, 255))
                        if x + 2 < canvas_width:
                            draw.line((x + 2, 0, x + 2, canvas_height - 10), fill=rgba, width=1)

                self._pose_timeline2_photo = ImageTk.PhotoImage(img)
                self._pose_timeline2_image_id = self.timeline2_canvas.create_image(
                    0, 0, anchor="nw", image=self._pose_timeline2_photo
                )

            self._timeline2_dirty = False
            self._timeline2_last_limb = "POSE_3D"
            self._timeline2_canvas_size = (canvas_width, canvas_height)
            self._timeline2_playhead_id = None
            self._pose_timeline2_scale_overlay_id = None

        current_pos = (self.video.current_frame / self.video.total_frames) * canvas_width if self.video.total_frames else 0
        if self._timeline2_playhead_id is None:
            self._timeline2_playhead_id = self.timeline2_canvas.create_line(
                current_pos, 0, current_pos, canvas_height, fill="dodgerblue", width=2
            )
        else:
            self.timeline2_canvas.coords(self._timeline2_playhead_id, current_pos, 0, current_pos, canvas_height)

        scale_min = 0.7
        scale_max = 1.3
        current_scale = float(self._get_effective_pose_scale(self.video.current_frame)[1] or 1.0)
        scale_ratio = (current_scale - scale_min) / (scale_max - scale_min)
        scale_ratio = max(0.0, min(1.0, scale_ratio))
        y_scale = int(round((canvas_height - 10) - ((canvas_height - 14) * scale_ratio)))
        if self._pose_timeline2_scale_overlay_id is None:
            self._pose_timeline2_scale_overlay_id = self.timeline2_canvas.create_oval(
                current_pos - 2,
                y_scale - 2,
                current_pos + 2,
                y_scale + 2,
                fill="#113a5c",
                outline="",
            )
        else:
            self.timeline2_canvas.coords(
                self._pose_timeline2_scale_overlay_id,
                current_pos - 2,
                y_scale - 2,
                current_pos + 2,
                y_scale + 2,
            )

    def draw_timeline(self):
        with self.perf.time("draw_timeline"):
            if not (self.video and self.video.total_frames > 0):
                return
            if self.is_pose_mode():
                self._draw_pose_timeline()
                return
            canvas_width = self.timeline_canvas.winfo_width()
            canvas_height = self.timeline_canvas.winfo_height()
            limb = self.option_var_1.get()
            zone = self.video.current_frame_zone
            needs_full = (
                self._timeline_dirty
                or self._timeline_last_zone != zone
                or self._timeline_last_limb != limb
                or self._timeline_canvas_size != (canvas_width, canvas_height)
            )

            sector_width = canvas_width / self.video.number_frames_in_zone if self.video.number_frames_in_zone else 1
            offset = self.video.number_frames_in_zone * zone
            top = 0; bottom = canvas_height

            if needs_full:
                self.timeline_canvas.delete("all")
                data_source = {
                    'RH': self.video.dataRH, 'LH': self.video.dataLH, 'RL': self.video.dataRL, 'LL': self.video.dataLL
                }
                data = data_source.get(limb, self.video.data)
                self.is_touch_timeline = False if zone == 0 else self.video.touch_to_next_zone[zone]

                def get_color(frame_idx, data):
                    if frame_idx > self.video.total_frames: return 'black'
                    details = data.get(frame_idx, {})
                    xs = details.get('X', [])
                    if not xs:
                        return self.color_during if self.is_touch_timeline else 'lightgrey'
                    if len(xs) >= 1 and xs[0] is not None:
                        if details.get('Onset') == 'ON':
                            self.is_touch_timeline = True; return 'lightgreen'
                        else:
                            self.is_touch_timeline = False; return '#E57373'
                    return self.color_during if self.is_touch_timeline else 'lightgrey'

                for frame in range(self.video.number_frames_in_zone):
                    left = frame * sector_width
                    right = left + sector_width
                    frame_offset = frame + offset
                    color = get_color(frame_offset, data)
                    self.timeline_canvas.create_rectangle(left, top, right, bottom, fill=color, outline='black')
                    param_color = self.parameter_color_at_frame(frame_offset)
                    if param_color is not None:
                        mid_x = (left + right) / 2
                        self.timeline_canvas.create_line(mid_x, top, mid_x, bottom, fill=param_color, width=2)

                    # NEW: per-limb ticks for Param1..3 on this frame
                    colors = self.limb_parameter_colors_at_frame(frame_offset)
                    mid_x = (left + right) / 2
                    offsets = (-2, 0, 2)
                    for col, dx in zip(colors, offsets):
                        if col:
                            self.timeline_canvas.create_line(mid_x + dx, top, mid_x + dx, bottom, fill=col, width=2)

                    if frame == self.video.number_frames_in_zone - 1:
                        if self.video.current_frame_zone + 1 < len(self.video.touch_to_next_zone):
                            self.video.touch_to_next_zone[self.video.current_frame_zone + 1] = (color == self.color_during)
                        elif self.video.current_frame_zone + 1 == len(self.video.touch_to_next_zone):
                            self.video.touch_to_next_zone.append(color == self.color_during)

                # keep the original extra ticks behavior
                colors = self.limb_parameter_colors_at_frame(frame_offset)
                mid_x = (left + right) / 2
                offsets = (-2, 0, 2)  # horizontal pixel offsets for Param1..3
                for col, dx in zip(colors, offsets):
                    if col:
                        self.timeline_canvas.create_line(mid_x + dx, top, mid_x + dx, bottom, fill=col, width=2)

                self._timeline_dirty = False
                self._timeline_last_zone = zone
                self._timeline_last_limb = limb
                self._timeline_canvas_size = (canvas_width, canvas_height)
                self._timeline_playhead_id = None

            # Update playhead only
            frame_in_zone = self.video.current_frame - offset
            if 0 <= frame_in_zone < self.video.number_frames_in_zone:
                left = frame_in_zone * sector_width
                right = left + sector_width
                if self._timeline_playhead_id is None:
                    self._timeline_playhead_id = self.timeline_canvas.create_rectangle(
                        left, top, right, bottom, fill='dodgerblue', outline='black'
                    )
                else:
                    self.timeline_canvas.coords(self._timeline_playhead_id, left, top, right, bottom)

    def draw_timeline2(self):
        with self.perf.time("draw_timeline2"):
            if not (self.video and self.video.total_frames > 0):
                return
            if self.is_pose_mode():
                self._draw_pose_timeline2()
                return

            canvas_width  = self.timeline2_canvas.winfo_width()
            canvas_height = self.timeline2_canvas.winfo_height()
            limb = self.option_var_1.get()  # currently selected limb ("LH","RH","LL","RL")
            needs_full = (
                self._timeline2_dirty
                or self._timeline2_last_limb != limb
                or self._timeline2_canvas_size != (canvas_width, canvas_height)
            )

            if needs_full:
                self.timeline2_canvas.delete("all")
                # --- First pass: collect intervals (for yellow fill) and all lines to draw later ---
                on_lines   = []      # x positions of On (green)
                off_lines  = []      # x positions of Off (red)
                intervals  = []      # [(x_on, x_off), ...]
                param_lines = []     # [(x, color)] from GLOBAL parameter_color_at_frame
                limb_param_lines = []  # [(x+dx, color)] from limb_parameter_colors_at_frame
                active_on_x = None

                # Deterministic left->right scan across frames that exist in memory
                for frame in sorted(self.video.frames.keys()):
                    bundle = self.video.frames.get(frame, {})
                    details = bundle.get(limb, {}) if isinstance(bundle, dict) else {}

                    x = (frame / self.video.total_frames) * canvas_width

                    # --- Onset/Offset collection for intervals + edge markers (SELECTED LIMB ONLY) ---
                    onset_val = details.get('Onset')
                    if onset_val == 'ON':
                        on_lines.append(x)
                        if active_on_x is None:
                            active_on_x = x
                    elif onset_val == 'OFF':
                        off_lines.append(x)
                        if active_on_x is not None:
                            x1, x2 = (active_on_x, x) if active_on_x <= x else (x, active_on_x)
                            if abs(x2 - x1) >= 1:
                                intervals.append((x1, x2))
                            active_on_x = None

                    # --- GLOBAL Parameter markers (non-limb) — always visible ---
                    col = self.parameter_color_at_frame(frame)
                    if col is not None:
                        param_lines.append((x, col))

                    # --- Limb-specific parameter ticks (only when a limb is selected) ---
                    #      small +/-2px offsets so the three limb-params can be distinguished
                    if limb in ("LH", "RH", "LL", "RL") and hasattr(self, "limb_parameter_colors_at_frame"):
                        colors = self.limb_parameter_colors_at_frame(frame)  # returns up to 3 colors or None
                        for dx, c in zip((-2, 0, 2), colors):
                            if c:
                                limb_param_lines.append((x + dx, c))

                # --- Draw order: 1) fills, 2) global & limb param lines, 3) On/Off edges, 4) playhead ---
                # 1) Yellow fills
                for x1, x2 in intervals:
                    self.timeline2_canvas.create_rectangle(x1, 0, x2, canvas_height, fill='yellow', outline='')

                # 2) Global parameter lines
                for x, c in param_lines:
                    self.timeline2_canvas.create_line(x, 0, x, canvas_height, fill=c, width=2)

                #    Limb-specific parameter ticks for the selected limb
                for x, c in limb_param_lines:
                    self.timeline2_canvas.create_line(x, 0, x, canvas_height, fill=c, width=2)

                # 3) On/Off edge markers
                for x in on_lines:
                    self.timeline2_canvas.create_line(x, 0, x, canvas_height, fill='green', width=1)
                for x in off_lines:
                    self.timeline2_canvas.create_line(x, 0, x, canvas_height, fill='red', width=1)

                self._timeline2_dirty = False
                self._timeline2_last_limb = limb
                self._timeline2_canvas_size = (canvas_width, canvas_height)
                self._timeline2_playhead_id = None

            # Current frame indicator only
            current_pos = (self.video.current_frame / self.video.total_frames) * canvas_width
            margin = 2
            current_pos = min(max(current_pos, margin), canvas_width - margin)
            if self._timeline2_playhead_id is None:
                self._timeline2_playhead_id = self.timeline2_canvas.create_line(
                    current_pos, 0, current_pos, canvas_height, fill='dodgerblue', width=2
                )
            else:
                self.timeline2_canvas.coords(self._timeline2_playhead_id, current_pos, 0, current_pos, canvas_height)
    
    def update_frame_counter(self):
        if self.video:
            current_frame_text = f"{self.video.current_frame} / {self.video.total_frames}"
            self.frame_counter_label.config(text=current_frame_text)

            def format_time(ms):
                hours, remainder = divmod(ms, 3600000)
                minutes, remainder = divmod(remainder, 60000)
                seconds, milliseconds = divmod(remainder, 1000)
                if hours > 0:
                    return f"{hours}:{minutes:02}:{seconds:02}.{milliseconds:03}"
                else:
                    return f"{minutes}:{seconds:02}.{milliseconds:03}"

            current_time = self.video.current_frame / self.video.frame_rate * 1000
            total_time = self.video.total_frames / self.video.frame_rate * 1000
            self.time_counter_label.config(text=f"{format_time(int(current_time))} / {format_time(int(total_time))}")

        else:
            self.frame_counter_label.config(text="0 / 0")

        self.video.current_frame_zone = int(self.video.current_frame / self.video.number_frames_in_zone)

    def _format_duration(self, seconds):
        if seconds is None:
            return "--:--"
        seconds = int(max(0, seconds))
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02}:{secs:02}"
        return f"{minutes}:{secs:02}"

    def _open_frame_progress_window(self):
        win = tk.Toplevel(self)
        win.title("Preparing Frames")
        win.geometry("520x170")
        win.resizable(False, False)
        win.transient(self)

        title = tk.Label(win, text="Preparing frames...", font=("Segoe UI", 11))
        title.pack(pady=(12, 6))
        status = tk.Label(win, text="Starting...", font=("Segoe UI", 10))
        status.pack()
        bar = ttk.Progressbar(win, mode="determinate", length=460)
        bar.pack(pady=8)
        time_label = tk.Label(win, text="Elapsed: 0:00 | ETA: --:--", font=("Segoe UI", 9))
        time_label.pack()
        win.update_idletasks()

        def update(count, total, stage, elapsed_s):
            if not win.winfo_exists():
                return
            try:
                total = max(1, int(total))
                count = min(int(count), total)
                bar["maximum"] = total
                bar["value"] = count
                pct = (count / total) * 100 if total else 0
                status.config(text=f"{stage}: {count} / {total} ({pct:.1f}%)")
                eta_s = None
                if count > 0:
                    rate = elapsed_s / count
                    eta_s = max(0.0, (total - count) * rate)
                time_label.config(
                    text=f"Elapsed: {self._format_duration(elapsed_s)} | ETA: {self._format_duration(eta_s)}"
                )
                win.update_idletasks()
                win.update()
            except tk.TclError:
                pass

        def close():
            if win.winfo_exists():
                win.destroy()

        return update, close

    def _open_video_copy_progress_window(self):
        win = tk.Toplevel(self)
        win.title("Copying Video")
        win.geometry("520x170")
        win.resizable(False, False)
        win.transient(self)

        title = tk.Label(win, text="Copying video to project...", font=("Segoe UI", 11))
        title.pack(pady=(12, 6))
        status = tk.Label(win, text="Starting...", font=("Segoe UI", 10))
        status.pack()
        bar = ttk.Progressbar(win, mode="determinate", length=460)
        bar.pack(pady=8)
        time_label = tk.Label(win, text="Elapsed: 0:00 | ETA: --:--", font=("Segoe UI", 9))
        time_label.pack()
        win.update_idletasks()

        def update(count, total, stage, elapsed_s):
            if not win.winfo_exists():
                return
            try:
                total = max(1, int(total))
                count = min(int(count), total)
                bar["maximum"] = total
                bar["value"] = count
                pct = (count / total) * 100 if total else 0
                status.config(text=f"{stage}: {count} / {total} ({pct:.1f}%)")
                eta_s = None
                if count > 0:
                    rate = elapsed_s / count
                    eta_s = max(0.0, (total - count) * rate)
                time_label.config(
                    text=f"Elapsed: {self._format_duration(elapsed_s)} | ETA: {self._format_duration(eta_s)}"
                )
                win.update_idletasks()
                win.update()
            except tk.TclError:
                pass

        def close():
            if win.winfo_exists():
                win.destroy()

        return update, close

    def _open_data_progress_window(self):
        win = tk.Toplevel(self)
        win.title("Loading Data")
        win.geometry("520x170")
        win.resizable(False, False)
        win.transient(self)

        title = tk.Label(win, text="Loading labeled data...", font=("Segoe UI", 11))
        title.pack(pady=(12, 6))
        status = tk.Label(win, text="Starting...", font=("Segoe UI", 10))
        status.pack()
        bar = ttk.Progressbar(win, mode="determinate", length=460)
        bar.pack(pady=8)
        time_label = tk.Label(win, text="Elapsed: 0:00 | ETA: --:--", font=("Segoe UI", 9))
        time_label.pack()
        win.update_idletasks()

        def update(count, total, stage, elapsed_s):
            if not win.winfo_exists():
                return
            try:
                total = max(1, int(total))
                count = min(int(count), total)
                bar["maximum"] = total
                bar["value"] = count
                pct = (count / total) * 100 if total else 0
                status.config(text=f"{stage}: {count} / {total} ({pct:.1f}%)")
                eta_s = None
                if count > 0:
                    rate = elapsed_s / count
                    eta_s = max(0.0, (total - count) * rate)
                time_label.config(
                    text=f"Elapsed: {self._format_duration(elapsed_s)} | ETA: {self._format_duration(eta_s)}"
                )
                win.update_idletasks()
                win.update()
            except tk.TclError:
                pass

        def close():
            if win.winfo_exists():
                win.destroy()

        return update, close

    def _copy_file_with_progress(self, src_path, dest_path, progress_cb, chunk_size=8 * 1024 * 1024):
        total_bytes = os.path.getsize(src_path)
        copied = 0
        start_time = time.time()

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(src_path, "rb") as src_file, open(dest_path, "wb") as dest_file:
            while True:
                chunk = src_file.read(chunk_size)
                if not chunk:
                    break
                dest_file.write(chunk)
                copied += len(chunk)
                if progress_cb:
                    progress_cb(copied, total_bytes, "Copying video", time.time() - start_time)
        shutil.copystat(src_path, dest_path, follow_symlinks=True)

    def _prepare_video_copy(self, source_path):
        videos_dir = os.path.join("Videos")
        os.makedirs(videos_dir, exist_ok=True)
        dest_path = os.path.join(videos_dir, os.path.basename(source_path))

        if os.path.abspath(source_path) == os.path.abspath(dest_path):
            print(f"INFO: Video already inside project Videos folder: {dest_path}")
            return dest_path

        if os.path.exists(dest_path):
            try:
                src_size = os.path.getsize(source_path)
                dest_size = os.path.getsize(dest_path)
                if src_size != dest_size:
                    messagebox.showwarning(
                        "Video Copy Skipped",
                        "A video with the same name already exists in the Videos folder.\n"
                        "Using the existing copy to avoid overwriting."
                    )
            except Exception:
                print("WARN: Could not compare video sizes; using existing copy.")
            print(f"INFO: Video already exists in Videos folder: {dest_path}")
            return dest_path

        progress_update, progress_close = self._open_video_copy_progress_window()
        try:
            self._copy_file_with_progress(source_path, dest_path, progress_update)
        except Exception as exc:
            print(f"ERROR: Failed to copy video: {exc}")
            messagebox.showerror("Video Copy Failed", f"Failed to copy video:\n{exc}")
            return None
        finally:
            progress_close()

        print(f"INFO: Copied video to {dest_path}")
        return dest_path

    # === Frame Loading, Display & Buffer =======================================
    def background_update(self, frame_number=None):
        while True:
            # Sleep up to 10 ms unless a jump pokes us awake earlier.
            self._priority_event.wait(timeout=0.01)
            self._priority_event.clear()
            if self.video is None or self.video.frames_dir is None:
                continue
            with self.perf.time("background_update"):
                current_frame = self.video.current_frame
                if current_frame < 0 or current_frame > self.video.total_frames:
                    return

                # Capture per-tick context once. Workers must NOT touch Tk widgets,
                # so we read winfo_width / winfo_height here on the bg thread (same
                # risk profile as before this change — bg thread already did this).
                frames_dir = self.video.frames_dir
                display_w = self.video_frame.winfo_width()
                display_h = self.video_frame.winfo_height()
                downscale = float(getattr(self, "video_downscale", 1.0) or 1.0)
                gen = self._buffer_gen

                # 1) Load the currently-visible frame first (synchronously) so the
                #    user sees their jump destination ASAP, then fire a paint.
                current_frame_loaded = current_frame in self.img_buffer
                if not current_frame_loaded:
                    with self.perf.time("priority_load"):
                        self._load_frame_to_buffer(
                            current_frame, frames_dir, display_w, display_h, downscale, gen
                        )
                    if current_frame in self.img_buffer:
                        self.after(0, self.display_first_frame)
                        current_frame_loaded = True

                # 2) Honour an explicit priority hint (e.g. _request_buffered_step
                #    polling pre-loads the jump target before next_frame() is called).
                priority = self._priority_frame
                self._priority_frame = None
                if (priority is not None
                        and priority != current_frame
                        and 0 <= priority <= self.video.total_frames
                        and priority not in self.img_buffer):
                    with self.perf.time("priority_load"):
                        self._load_frame_to_buffer(
                            priority, frames_dir, display_w, display_h, downscale, gen
                        )

                # 3) Asymmetric, velocity-aware prefetch window. Same direction as
                #    the user's last navigation step gets a wider lookahead so a
                #    second jump in that direction lands in cache.
                base_ahead, base_behind = 50, 30
                jump = max(1, getattr(self, "jump_frame_count", 1))
                sign = self._last_step_sign
                if sign > 0:
                    ahead = max(base_ahead, jump * 2)
                    behind = max(10, base_behind // 2)
                elif sign < 0:
                    ahead = max(10, base_ahead // 2)
                    behind = max(base_behind, jump * 2)
                else:
                    ahead, behind = base_ahead, base_behind
                start_frame = max(0, current_frame - behind)
                end_frame = min(self.video.total_frames, current_frame + ahead)

                # 4) Submit prefetch loads to the worker pool. Forward window first
                #    (most likely direction of travel), then backward.
                for i in range(current_frame + 1, end_frame + 1):
                    self._maybe_submit_load(i, frames_dir, display_w, display_h, downscale, gen)
                for i in range(current_frame - 1, start_frame - 1, -1):
                    self._maybe_submit_load(i, frames_dir, display_w, display_h, downscale, gen)

                # 5) Trim out-of-range frames + enforce the byte budget. Hard cap
                #    scales with the asymmetric window so a wide forward prefetch
                #    isn't immediately undone.
                buffer_range_behind = max(200, behind * 4)
                buffer_range_ahead = max(200, ahead * 4)
                min_keep = max(0, current_frame - buffer_range_behind)
                max_keep = min(self.video.total_frames, current_frame + buffer_range_ahead)
                with self._buffer_lock:
                    frames_to_remove = [k for k in self.img_buffer if k < min_keep or k > max_keep]
                    for k in frames_to_remove:
                        self._buffer_remove_frame(k)
                    self._evict_buffer_to_budget(current_frame)

                # 6) Update the status pill.
                if current_frame_loaded:
                    self._set_loading_label_async("Buffer Loaded", "lightgreen")
                else:
                    self._set_loading_label_async("Buffer Loading", "#E57373")

                # 7) buffer_ready gates the playback thread.
                buffer_ready = current_frame_loaded
                if buffer_ready:
                    max_check = min(self.video.total_frames, current_frame + PLAYBACK_BUFFER_AHEAD)
                    for i in range(current_frame, max_check + 1):
                        if i not in self.img_buffer:
                            buffer_ready = False
                            break
                self.buffer_ready = buffer_ready

    def background_update_play(self):
        import time
        while True:
            if self.play and self.video is not None:
                current_frame = self.video.current_frame
                if current_frame not in self.img_buffer:
                    self.buffer_ready = False
                    time.sleep(PLAYBACK_BUFFER_PAUSE_S)
                    continue
                next_frame = min(self.video.total_frames, current_frame + 1)
                if next_frame not in self.img_buffer:
                    self.buffer_ready = False
                    time.sleep(PLAYBACK_BUFFER_PAUSE_S)
                    continue
                if not self.buffer_ready:
                    time.sleep(PLAYBACK_BUFFER_PAUSE_S)
                    continue
                start = time.perf_counter()
                self.next_frame(1, play=True)
                if self.video.current_frame % 10 == 0:
                    self.after(0, self.draw_timeline)
                interval = 1.0 / self.frame_rate if self.frame_rate else 0.04
                elapsed = time.perf_counter() - start
                time.sleep(max(0.0, interval - elapsed))
            else:
                time.sleep(0.05)

    def _resize_for_buffer(self, img, display_width, display_height, downscale):
        """Tk-free resize used by worker threads. Pure CPU work — no widget calls."""
        if display_width <= 0 or display_height <= 0:
            return img
        original_width, original_height = img.size
        aspect_ratio = original_width / original_height

        if downscale <= 0:
            downscale = 1.0

        target_width = max(1, int(original_width / downscale))
        target_height = max(1, int(original_height / downscale))

        max_width = min(display_width, target_width)
        max_height = min(display_height, target_height)

        if max_width / max_height > aspect_ratio:
            new_width = int(max_height * aspect_ratio); new_height = max_height
        else:
            new_width = max_width; new_height = int(max_width / aspect_ratio)
        return img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    def resize_frame(self, img):
        with self.perf.time("resize_frame"):
            display_width = self.video_frame.winfo_width()
            display_height = self.video_frame.winfo_height()
            downscale = float(getattr(self, "video_downscale", 1.0) or 1.0)
            resized = self._resize_for_buffer(img, display_width, display_height, downscale)
            self.old_width = resized.width
            self.old_height = resized.height
            return resized

    def _load_frame_to_buffer(self, frame_number, frames_dir, display_w, display_h, downscale, gen):
        """Disk read + JPEG decode + resize + buffer store. Safe to run on any thread.

        Stores the result only if the buffer generation still matches (gen == self._buffer_gen),
        i.e. no _buffer_reset happened mid-decode. Always discards from _inflight_frames.
        """
        from PIL import Image
        try:
            with self.perf.time("load_frame_total"):
                frame_path = os.path.join(frames_dir, f"frame{frame_number}.jpg")
                with self.perf.time("load_frame_open"):
                    with Image.open(frame_path) as opened:
                        img = opened.copy()
                with self.perf.time("load_frame_resize"):
                    img = self._resize_for_buffer(img, display_w, display_h, downscale)
                try:
                    bytes_per_pixel = max(1, len(img.getbands()))
                except Exception:
                    bytes_per_pixel = 4
                est_bytes = int(img.width * img.height * bytes_per_pixel)
                with self._buffer_lock:
                    if gen == self._buffer_gen:
                        self._buffer_store_frame(frame_number, img, est_bytes)
                    self._inflight_frames.discard(frame_number)
        except Exception as e:
            with self._buffer_lock:
                self._inflight_frames.discard(frame_number)
            print(f"ERROR: Opening or processing frame {frame_number}: {str(e)}")

    def _maybe_submit_load(self, frame_number, frames_dir, display_w, display_h, downscale, gen):
        """Submit a prefetch load to the worker pool, deduping against in-flight + cached frames."""
        if frame_number < 0 or self.video is None or frame_number > self.video.total_frames:
            return
        with self._buffer_lock:
            if frame_number in self.img_buffer or frame_number in self._inflight_frames:
                return
            self._inflight_frames.add(frame_number)
        try:
            self._loader_pool.submit(
                self._load_frame_to_buffer,
                frame_number, frames_dir, display_w, display_h, downscale, gen,
            )
        except RuntimeError:
            # Pool was shut down (e.g. on app close); back out the inflight reservation.
            with self._buffer_lock:
                self._inflight_frames.discard(frame_number)

    def display_first_frame(self, frame_number=None):
        with self.perf.time("display_first_frame"):
            previous_frame = self._last_displayed_frame
            if frame_number is None:
                frame_number = self.video.current_frame
            else:
                self.video.current_frame = frame_number
            if frame_number < 0 or frame_number > self.video.total_frames:
                print("ERROR: Frame number out of bounds."); return
            moving_forward = previous_frame is None or frame_number > previous_frame
            moving_backward = previous_frame is not None and frame_number < previous_frame
            if frame_number in self.img_buffer:
                pil_img = self.img_buffer[frame_number]
                with self.perf.time("display_frame_photo"):
                    photo_img = ImageTk.PhotoImage(pil_img)
                if hasattr(self, 'frame_label') and self.frame_label:
                    self.frame_label.configure(image=photo_img)
                else:
                    self.frame_label = tk.Label(self.video_frame, image=photo_img)
                    self.frame_label.pack(expand=True)
                self.loading_label.config(text="Buffer Loaded", bg='lightgreen')
                self.image = photo_img
            else:
                print("INFO: Frame not in buffer.")
                self.loading_label.config(text="Buffer Loading", bg='#E57373')

            self.update_note_entry()
            self.update_frame_counter()
            self.update_limb_parameter_buttons()
            self.update_button_colors()
            if self.is_pose_mode():
                bundle = self._ensure_bundle(self.video.current_frame)
                bundle_scale = float(bundle.get("ScaleRaw", 1.0) or 1.0)
                bundle_auto = bool(bundle.get("ScaleAutoCarry", False))
                carry_scale = float(self.current_pose_scale)
                carry_differs = abs(bundle_scale - carry_scale) > 1e-9
                if (
                    not self.play
                    and self._pose_scale_carry_active
                    and bundle.get("ScaleSet")
                    and bundle_auto
                    and carry_differs
                    and moving_forward
                ):
                    self._log_pose_scale(
                        f"overwrite auto frame={self.video.current_frame} old={bundle_scale:.2f} new={carry_scale:.2f}"
                    )
                    self._set_pose_scale_for_frame(
                        self.video.current_frame,
                        self.current_pose_scale,
                        redraw_overview=False,
                        auto_carried=True,
                    )
                elif bundle.get("ScaleSet"):
                    self.current_pose_scale = float(bundle.get("ScaleRaw", 1.0) or 1.0)
                    self._pose_scale_carry_active = True
                    source = "auto-carry" if bundle_auto else "manual"
                    self._log_pose_scale(
                        f"adopt frame={self.video.current_frame} scale={self.current_pose_scale:.2f} source={source}"
                    )
                elif not self.play and self._pose_scale_carry_active and moving_forward:
                    self._log_pose_scale(
                        f"carry new frame={self.video.current_frame} scale={self.current_pose_scale:.2f}"
                    )
                    self._set_pose_scale_for_frame(
                        self.video.current_frame,
                        self.current_pose_scale,
                        redraw_overview=False,
                        auto_carried=True,
                    )
                else:
                    self._log_pose_scale(
                        f"leave frame={self.video.current_frame} scale={bundle_scale:.2f} carry_active={self._pose_scale_carry_active} "
                        f"forward={moving_forward} backward={moving_backward}"
                    )
                self._updating_scale_widget = True
                try:
                    if getattr(self, "scale_var", None) is not None:
                        self.scale_var.set(self.current_pose_scale)
                finally:
                    self._updating_scale_widget = False
                self.update_pose_scale_label()
                self.render_pose_canvas()
            self._last_displayed_frame = frame_number

    def _buffer_reset(self):
        # Lock so concurrent workers can't store into a half-cleared buffer.
        # Bumps _buffer_gen so any in-flight worker decoded under the OLD video
        # discards its result instead of polluting the new buffer.
        with self._buffer_lock:
            if hasattr(self, "img_buffer"):
                self.img_buffer.clear()
            if hasattr(self, "img_buffer_bytes"):
                self.img_buffer_bytes.clear()
            self.img_buffer_total = 0
            if hasattr(self, "_inflight_frames"):
                self._inflight_frames.clear()
            if hasattr(self, "_buffer_gen"):
                self._buffer_gen += 1

    def _buffer_remove_frame(self, frame_number):
        with self._buffer_lock:
            if frame_number in self.img_buffer:
                del self.img_buffer[frame_number]
            if hasattr(self, "img_buffer_bytes"):
                removed = self.img_buffer_bytes.pop(frame_number, 0)
                self.img_buffer_total = max(0, self.img_buffer_total - removed)

    def _buffer_store_frame(self, frame_number, photo_img, est_bytes):
        with self._buffer_lock:
            if not hasattr(self, "img_buffer_bytes"):
                self.img_buffer_bytes = {}
            if frame_number in self.img_buffer_bytes:
                self.img_buffer_total = max(0, self.img_buffer_total - self.img_buffer_bytes.get(frame_number, 0))
            self.img_buffer[frame_number] = photo_img
            self.img_buffer_bytes[frame_number] = est_bytes
            self.img_buffer_total = self.img_buffer_total + est_bytes

    def _evict_buffer_to_budget(self, current_frame):
        limit = BUFFER_MAX_BYTES
        if limit is None or limit <= 0:
            return
        with self._buffer_lock:
            if self.img_buffer_total <= limit:
                return
            candidates = sorted(self.img_buffer.keys(), key=lambda k: abs(k - current_frame), reverse=True)
            for k in candidates:
                if k == current_frame:
                    continue
                self._buffer_remove_frame(k)
                if self.img_buffer_total <= limit:
                    break

    def _video_time_meta_path(self, data_dir, video_name):
        return os.path.join(data_dir, f"{video_name}_metadata.json")

    def _load_video_time(self, data_dir, video_name):
        path = self._video_time_meta_path(data_dir, video_name)
        if not os.path.exists(path):
            return 0.0
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f) or {}
            return float(payload.get("Total Labeling Time (seconds)", 0.0))
        except Exception as e:
            print(f"WARNING: Failed to load labeling time: {e}")
            return 0.0

    def _write_video_time(self, data_dir, video_name, total_seconds):
        path = self._video_time_meta_path(data_dir, video_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "Total Labeling Time (hours)": round(float(total_seconds) / 3600.0, 4),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"WARNING: Failed to save labeling time: {e}")

    def _start_video_timer(self, data_dir, video_name):
        self._video_time_total_s = self._load_video_time(data_dir, video_name)
        self._video_session_start = time.monotonic()

    def _current_video_time_s(self):
        total = float(getattr(self, "_video_time_total_s", 0.0) or 0.0)
        start = getattr(self, "_video_session_start", None)
        if start is None:
            return total
        return total + (time.monotonic() - start)

    def _persist_video_time(self):
        if self.video is None or self.video_name is None:
            return
        data_dir = os.path.join("Labeled_data", self.video_name, "data")
        total = self._current_video_time_s()
        self._write_video_time(data_dir, self.video_name, total)
        self._video_time_total_s = total
        self._video_session_start = time.monotonic()

    def _finalize_video_time(self):
        if self.video is None or self.video_name is None:
            return
        data_dir = os.path.join("Labeled_data", self.video_name, "data")
        total = self._current_video_time_s()
        self._write_video_time(data_dir, self.video_name, total)
        self._video_time_total_s = total
        self._video_session_start = None

    # === Parameter Toggles & Coloring ==========================================
    def update_button_colors(self):
        if self.video is None:
            return
        idx = self.video.current_frame
        b = self.video.frames.get(idx, {})
        params = (b.get("Params") or {})

        for i, btn in ((1, self.par1_btn), (2, self.par2_btn), (3, self.par3_btn)):
            key = self._param_key_for_index(i)
            state = params.get(key)
            if state == "ON":
                btn.config(bg="lightgreen")
            elif state == "OFF":
                btn.config(bg="#E57373")
            else:
                btn.config(bg="lightgrey")

    def parameter_dic_insert(self, parameter_index: int):
        """Toggle Param_i (1..3) for the CURRENT frame directly on the bundle."""
        if self.video is None:
            return
        idx = self.video.current_frame
        b = self._ensure_bundle(idx)
        params = self._ensure_params(b)

        key = self._param_key_for_index(parameter_index)
        prev = params.get(key)
        new_state = self._param_next_state(prev)
        params[key] = new_state
        b["Params"] = params

        # color the right button immediately
        button = {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn}[parameter_index]
        if new_state == "ON":
            button.config(bg="lightgreen")
        elif new_state == "OFF":
            button.config(bg="#E57373")
        else:
            button.config(bg="lightgrey")

        # mark frame dirty, print, and refresh timeline
        self.mark_bundle_changed(idx)
        self.draw_timeline()

    def toggle_limb_parameter(self, param_number: int):
        if self.is_pose_mode():
            return
        limb = self.option_var_1.get()
        frame = self.video.current_frame

        # ensure bundle & this limb's record exist
        b = self._ensure_bundle(frame)
        rec = b.get(limb) or {"X": [], "Y": [], "Onset": "", "Bodypart": limb, "Look": "", "Zones": [], "Touch": None}
        b[limb] = rec

        limb_params = self._ensure_limb_params(rec)
        key = self._limb_param_key_for_index(param_number)
        prev = limb_params.get(key)
        # None -> ON -> OFF -> None
        if prev is None or prev == "":
            new_state = "ON"
        elif prev == "ON":
            new_state = "OFF"
        elif prev == "OFF":
            new_state = "None"
        else:
            new_state = None
        limb_params[key] = new_state

        # reflect on button color
        btn = {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn}[param_number]
        if new_state == "ON":
            btn.config(bg="lightgreen")
        elif new_state == "OFF":
            btn.config(bg="#E57373")
        else:
            btn.config(bg="lightgray")

        # mark & redraw (so timeline updates)
        self.mark_bundle_changed(frame)
        self.draw_timeline()

    def update_limb_parameter_buttons(self):
        if self.is_pose_mode() or not self.video:
            return
        limb = self.option_var_1.get()
        frame = self.video.current_frame
        b = self.video.frames.get(frame, {})
        rec = b.get(limb, {}) if isinstance(b, dict) else {}
        limb_params = rec.get("LimbParams", {}) if isinstance(rec, dict) else {}

        for i, btn in ((1, self.limb_par1_btn), (2, self.limb_par2_btn), (3, self.limb_par3_btn)):
            key = self._limb_param_key_for_index(i)
            state = limb_params.get(key)
            if state == "ON":
                btn.config(bg="lightgreen")
            elif state == "OFF":
                btn.config(bg="#E57373")
            else:
                btn.config(bg="lightgray")
    
    def limb_parameter_colors_at_frame(self, frame):
        """Return Param1..3 colors for the SELECTED limb at a given frame."""
        if self.is_pose_mode():
            return []
        limb = self.option_var_1.get()
        b = self.video.frames.get(frame, {})
        rec = b.get(limb, {}) if isinstance(b, dict) else {}
        limb_params = rec.get("LimbParams", {}) if isinstance(rec, dict) else {}

        colors = []
        for i in (1, 2, 3):
            key = self._limb_param_key_for_index(i)
            val = limb_params.get(key)
            if val == "ON":
                colors.append("green")
            elif val == "OFF":
                colors.append("#E57373")
            else:
                colors.append(None)
        return colors

    # === Notes & Frame Selection ===============================================
    def select_frame(self):
        frame = self.note_entry.get()
        try:
            frame_int = int(frame)
        except ValueError:
            print("Error selecting frame: The frame number must be a valid integer.")
            self.note_entry.delete(0, 'end'); return
        if self.video is not None:
            if frame_int < 0 or frame_int > self.video.total_frames:
                print("Error selecting frame: Out of range!")
                self.note_entry.delete(0, 'end'); return
            self.video.current_frame = frame_int
            self.update_frame_counter()
            self.display_first_frame()
        else:
            print("Error selecting frame: No video loaded!")
        self.note_entry.delete(0, 'end')

    def save_note(self):
        print("INFO: Saving note...")
        self.enable_arrow_keys()

        idx = self.video.current_frame
        note_text = (self.note_entry.get() or "").strip()

        # ensure bundle exists, then update Note
        b = self._ensure_bundle(idx)

        prev = (b.get("Note") or "").strip()
        new_val = note_text if note_text else None

        if prev != (new_val or ""):
            b["Note"] = new_val
            # mark bundle dirty + print (uses your existing helpers)
            if hasattr(self, "mark_bundle_changed"):
                self.mark_bundle_changed(idx)
            elif isinstance(b, dict):
                b["Changed"] = True
            if hasattr(self, "notify_bundle_changed"):
                self.notify_bundle_changed(idx)

        
        print(f"INFO: Note saved for frame {idx}: {note_text}")
        try:
            import keyboard
            keyboard.press_and_release('tab')
        except Exception:
            pass

    def update_note_entry(self):
        if self.video is None:
            return
        idx = self.video.current_frame
        note_text = ""

        b = self.video.frames.get(idx)
        if isinstance(b, dict):
            note_text = (b.get("Note") or "")  # bundle-first

        # Fallback (only if you still have legacy self.video.notes around)
        if not note_text and hasattr(self.video, "notes"):
            note_text = self.video.notes.get(idx, "") or ""

        self.note_entry.delete(0, tk.END)
        self.note_entry.insert(0, note_text)

    # === Save / Export =========================================================
    def save_data(self):
        if not self.video or not self.video.frames_dir:
            print("INFO: Save skipped (no video loaded).")
            return
        self._persist_video_time()
        self.preview_before_save(changed_only=True)
        print("INFO: Saving (unified & export)...")
        
        base_dir = os.path.dirname(self.video.frames_dir)  # -> Labeled_data/<video>
        data_dir   = os.path.join(base_dir, "data")
        export_dir = os.path.join(base_dir, "export")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(export_dir, exist_ok=True)
        print(f"DEBUG: Base dir:   {base_dir}")
        print(f"DEBUG: Data dir:   {data_dir}")
        print(f"DEBUG: Export dir: {export_dir}")
        print(f"DEBUG: Frames dir: {self.video.frames_dir}")
        unified_path = os.path.join(data_dir, f"{self.video_name}_unified.csv")
        print(f"DEBUG: Writing unified dataset → {unified_path}")

        from data_utils import save_unified_dataset, export_from_unified, extract_zones_from_file
        if self.is_pose_mode():
            save_pose_dataset(unified_path, self.video.total_frames, self.video.frames)
            clothes_list = None
        else:
            save_unified_dataset(unified_path, self.video.total_frames, self.video.frames)
            clothes_path = self.video.clothes_file_path
            if not clothes_path and self.video.dataNotes_path_to_csv:
                clothes_path = self.video.dataNotes_path_to_csv.replace('_notes.csv', '_clothes.txt')
            clothes_list = extract_zones_from_file(clothes_path) if clothes_path else None
        export_path = os.path.join(export_dir, f"{self.video_name}_export.csv")
        print(f"DEBUG: Writing export dataset → {export_path}")

        # labeling_app.py (inside save_data, before export_from_unified call)
        param_labels = {
            "Parameter_1": (self.par1_btn.cget("text") or "Par1"),
            "Parameter_2": (self.par2_btn.cget("text") or "Par2"),
            "Parameter_3": (self.par3_btn.cget("text") or "Par3"),
        }
        limb_param_labels = None
        if self.limb_par1_btn and self.limb_par2_btn and self.limb_par3_btn:
            limb_param_labels = {
                "XX_Parameter_1": (self.limb_par1_btn.cget("text") or "LimbPar1"),
                "XX_Parameter_2": (self.limb_par2_btn.cget("text") or "LimbPar2"),
                "XX_Parameter_3": (self.limb_par3_btn.cget("text") or "LimbPar3"),
            }
        # NEW: write JSON sidecar with metadata (instead of stuffing CSV header)
        from data_utils import write_export_metadata
        meta_path = os.path.join(export_dir, f"{self.video_name}_metadata.json")
        write_export_metadata(
            meta_path=meta_path,
            program_version=self.video.program_version,
            video_name=self.video_name,
            labeling_mode=f"{self.labeling_mode} | {THREE_D_MODE}" if self.is_pose_mode() else self.labeling_mode,
            frame_rate=self.frame_rate,
            clothes_list=clothes_list,
            param_labels=param_labels,
            limb_param_labels=limb_param_labels,
            labeling_time_seconds=self._current_video_time_s(),
        )

        if self.is_pose_mode():
            export_pose_dataset(
                self.video.frames,
                export_path,
                total_frames=self.video.total_frames,
                frame_rate=self.frame_rate,
            )
        else:
            export_from_unified(
                self.video.frames,
                export_path,
                self.video.program_version,
                self.video_name,
                self.labeling_mode,
                self.frame_rate,
                clothes_list,
                total_frames=self.video.total_frames,
                param_labels=param_labels,
                limb_param_labels=limb_param_labels,
            )
        print("INFO: Save completed successfully.")
        for f, b in self.video.frames.items():
            if isinstance(b, dict) and b.get("Changed"):
                b["Changed"] = False
        print("DEBUG: Cleared bundle 'Changed' flags after save.")

    # === Analysis / Sort / Playback ============================================
    def analysis(self):
        if self.is_pose_mode():
            print("INFO: Analysis is disabled in 3D mismatch mode.")
            return
        if self.video:
            self.save_data()
            data_dir = os.path.dirname(self.video.dataRH_path_to_csv)
            base_dir = os.path.dirname(data_dir)
            plots_path = os.path.join(base_dir, "plots")
            analysis.do_analysis(data_dir, plots_path, self.video_name, debug=False, frame_rate=self.frame_rate)

    def play_video(self):
        if self.video is None:
            print("ERROR: First select video")
            return
        if not self.play:
            self.play = True
            if not self.play_thread_on:
                self.play_thread_on = True
                if not self.background_thread_play.is_alive():
                    self.background_thread_play.start()

    def stop_video(self):
        self.play = False

    def sort_frames(self):
        if self.is_pose_mode():
            print("INFO: Sort Frames is disabled in 3D mismatch mode.")
            return
        self.save_data()
        base_dir = os.path.dirname(os.path.dirname(self.video.dataRH_path_to_csv))
        csv_path = os.path.join(base_dir, "export", f"{self.video_name}_export.csv")
        images_dir = self.video.frames_dir
        output_dir = os.path.join(base_dir, "sorted_frames")
        os.makedirs(output_dir, exist_ok=True)
        try:
            process_touch_data_strict_transitions(csv_path, images_dir, output_dir)
            print(f"INFO: Sorted frames written to {output_dir}")
        except Exception as e:
            print(f"ERROR in sort_frames: {e}")

    # === Video Load & Init =====================================================
    def ask_labeling_mode(self):
        mode_window = tk.Toplevel(self)
        mode_window.title("Select Modes")
        mode_window.geometry("520x320")
        mode_window.grab_set()
        label = tk.Label(mode_window, text="Choose startup modes:", font=("Arial", 12))
        label.pack(pady=10)
        cfg = load_config()
        labeling_var = tk.StringVar(value=getattr(self, "labeling_mode", cfg.get("last_labeling_mode", "Normal")))
        annotation_var = tk.StringVar(value=getattr(self, "annotation_mode", cfg.get("annotation_mode", "touch")))

        tk.Label(mode_window, text="Labeling mode", font=("Arial", 10, "bold")).pack(pady=(5, 2))
        tk.Radiobutton(mode_window, text="Normal", variable=labeling_var, value="Normal").pack()
        tk.Radiobutton(mode_window, text="Reliability", variable=labeling_var, value="Reliability").pack()

        tk.Label(mode_window, text="Annotation mode", font=("Arial", 10, "bold")).pack(pady=(12, 2))
        tk.Radiobutton(mode_window, text="Touch", variable=annotation_var, value="touch").pack()
        tk.Radiobutton(mode_window, text=THREE_D_MODE, variable=annotation_var, value="pose_3d").pack()

        def set_mode():
            self.labeling_mode = labeling_var.get()
            self.annotation_mode = annotation_var.get()
            self._reset_zone_cache()
            bg = 'yellow' if self.labeling_mode == 'Reliability' else 'lightgreen'
            display_annotation = "3D" if self.is_pose_mode() else "Touch"
            self.mode_label.config(text=f"Mode: {self.labeling_mode} | {display_annotation}", bg=bg)
            cfg["last_labeling_mode"] = self.labeling_mode
            cfg["annotation_mode"] = self.annotation_mode
            save_config(cfg)
            self.rebuild_annotation_controls()
            self._set_sort_analysis_state()
            mode_window.destroy()

        tk.Button(mode_window, text="Continue", command=set_mode, width=18).pack(pady=16)
        mode_window.wait_window()

    def load_video(self):
        had_video = self.video is not None
        if self.video is not None:
            print("INFO: Saving before loading new video.")
            self.save_data()
            self.save_last_position()

        self.ask_labeling_mode()
        if not hasattr(self, 'labeling_mode'):
            print("INFO: No mode selected, cancelling video load."); return

        video_path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=(
                ("Video files", "*.mp4 *.MP4 *.mov *.MOV *.avi *.AVI *.mkv *.MKV *.flv *.FLV *.wmv *.WMV"),
                ("All files", "*.*"),
            ),
        )
        if not video_path: return

        copied_path = self._prepare_video_copy(video_path)
        if not copied_path:
            print("INFO: Video copy failed; cancelling load.")
            return
        video_path = copied_path
        if had_video:
            self._finalize_video_time()

        self.video = Video(video_path)
        self.current_pose_scale = 1.0
        self._pose_scale_carry_active = False
        self._last_displayed_frame = None
        cap = cv2.VideoCapture(video_path)
        self.video.frame_rate = round(cap.get(cv2.CAP_PROP_FPS), 1)
        cap.release()
        self.frame_rate = self.video.frame_rate
        self.framerate_label.config(text=f"Frame Rate: {self.frame_rate}")
        self.jump_frame_count = max(1, round(self.frame_rate * self.jump_seconds))
        print(f"INFO: Fast-jump set to {self.jump_frame_count} frames "
              f"({self.jump_seconds}s @ {self.frame_rate} fps)")
        self._refresh_jump_label()
        min_length_in_frames = self.minimal_touch_length * self.frame_rate / 1000
        self.min_touch_length_label.config(text=f"Minimal Touch Length: {min_length_in_frames}")
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        if self.is_pose_mode():
            video_name += "_3d"
        if self.labeling_mode == "Reliability":
            video_name += "_reliability"
        self.video_name = video_name

        base_dir = os.path.join("Labeled_data", video_name)
        data_dir = os.path.join(base_dir, "data")
        frames_dir = os.path.join(base_dir, "frames")
        plots_dir = os.path.join(base_dir, "plots")
        export_dir = os.path.join(base_dir, "export")
        for d in (data_dir, frames_dir, plots_dir, export_dir): os.makedirs(d, exist_ok=True)
        self.video.frames_dir = frames_dir
        self._start_video_timer(data_dir, video_name)

        # --- Unified-first load ---
        unified_path = os.path.join(data_dir, f"{video_name}_unified.csv")
        export_path  = os.path.join(export_dir, f"{video_name}_export.csv")
        print(f"INFO: load_video: unified_path={unified_path}", flush=True)
        print(f"INFO: load_video: export_path={export_path}", flush=True)

        from data_utils import (
            load_unified_dataset, empty_bundle,
            import_unified_from_export, save_unified_dataset
        )

        # Load unified (robust: handles 0-byte / header-only files).
        # Open a progress window covering the full data-load phase (unified
        # load + export-recovery) so the user sees progress on huge videos.
        data_progress_update, data_progress_close = self._open_data_progress_window()
        try:
            try:
                print("INFO: load_video: loading unified dataset...", flush=True)
                t_unified = time.time()
                if self.is_pose_mode():
                    self._reset_zone_cache()
                    self.video.frames = load_pose_dataset(unified_path) or {}
                else:
                    self._reset_zone_cache()
                    self.video.frames = load_unified_dataset(
                        unified_path, progress_cb=data_progress_update
                    ) or {}
                print(f"INFO: load_video: unified load done in {time.time() - t_unified:.1f}s "
                      f"({len(self.video.frames)} frames)", flush=True)
            except Exception:
                print("ERROR: load_video: exception while loading unified dataset:", flush=True)
                traceback.print_exc()
                sys.stdout.flush()
                self.video.frames = {}

            # Rebind LimbViews to the current dict (CRITICAL)
            self.video.dataRH._frames = self.video.frames
            self.video.dataLH._frames = self.video.frames
            self.video.dataRL._frames = self.video.frames
            self.video.dataLL._frames = self.video.frames

            # Fallback: if unified is empty but export exists, recover once from export.
            # We deliberately do NOT write the unified CSV here: on huge videos
            # (e.g. 300k+ frames) writing all rows blocks the UI thread for tens of
            # seconds. The next regular Save will materialize the unified file
            # naturally; until then we just keep the recovered dict in memory.
            if (not self.is_pose_mode()) and (not self.video.frames) and os.path.exists(export_path):
                print("INFO: Unified empty; importing from export for recovery…", flush=True)
                try:
                    t_recover = time.time()
                    self.video.frames = import_unified_from_export(
                        export_path, progress_cb=data_progress_update
                    ) or {}
                    print(f"INFO: Recovery import returned in {time.time() - t_recover:.1f}s", flush=True)
                except Exception:
                    print("ERROR: load_video: exception during import_unified_from_export:", flush=True)
                    traceback.print_exc()
                    sys.stdout.flush()
                    self.video.frames = {}

                # Rebind again to the recovered dict
                self.video.dataRH._frames = self.video.frames
                self.video.dataLH._frames = self.video.frames
                self.video.dataRL._frames = self.video.frames
                self.video.dataLL._frames = self.video.frames
                print(
                    f"INFO: Recovery loaded {len(self.video.frames)} frames in memory "
                    f"(unified CSV will be written on first Save).",
                    flush=True,
                )
        finally:
            data_progress_close()

        


        # Always set these paths (other features derive folders from them)
        for suffix in ['RH', 'LH', 'RL', 'LL']:
            csv_path = os.path.join(data_dir, f"{video_name}{suffix}.csv")
            setattr(self.video, f"data{suffix}_path_to_csv", csv_path)

        # If unified did not exist BUT legacy limb CSVs do, migrate them once into self.video.frames
        if (not self.is_pose_mode()) and not self.video.frames:
            print("INFO: No unified file found; attempting legacy CSV migration...")
            any_legacy = False
            for suffix in ['RH', 'LH', 'RL', 'LL']:
                csv_path = getattr(self.video, f"data{suffix}_path_to_csv")
                if os.path.exists(csv_path):
                    any_legacy = True
                    d = csv_to_dict(csv_path)
                    for fr, rec in d.items():
                        b = self.video.frames.setdefault(fr, empty_bundle())
                        b[suffix] = rec
            if any_legacy:
                print("INFO: Legacy limb CSVs merged into unified in-memory store.")
            else:
                print("INFO: Starting with an empty unified store.")

        # Parameter CSVs
        for name in ['parameter_1', 'parameter_2', 'parameter_3']:
            csv_path = os.path.join(data_dir, f"{video_name}{name}.csv")
            setattr(self.video, f"data{name}_path_to_csv", csv_path)

        

        # Names for parameters (update button text)
        load_parameter_names_into(
            self.video,
            {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn},
            {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn},
        )

        # Frames generation/check
        print("INFO: load_video: checking frames folder...", flush=True)
        if not check_items_count(frames_dir, self.video.total_frames):
            print("INFO: Number of frames is different, creating new frames", flush=True)
            progress_update, progress_close = self._open_frame_progress_window()
            try:
                create_frames(
                    video_path,
                    frames_dir,
                    self.labeling_mode,
                    self.video_name,
                    progress_cb=progress_update,
                )
            finally:
                progress_close()
        else:
            print("INFO: Number of frames is correct", flush=True)

        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._timeline_last_zone = None
        self._timeline_last_limb = None
        self._timeline2_last_limb = None
        self._timeline_canvas_size = (0, 0)
        self._timeline2_canvas_size = (0, 0)
        self._timeline_playhead_id = None
        self._timeline2_playhead_id = None

        print("INFO: load_video: restoring last position...", flush=True)
        self.restore_last_position(data_dir, video_name)

        print("INFO: load_video: drawing first frame & timelines...", flush=True)
        t_draw = time.time()
        self.display_first_frame()
        self.draw_timeline()
        self.draw_timeline2()
        print(f"INFO: load_video: initial draw done in {time.time() - t_draw:.1f}s", flush=True)
        self.name_label.config(
            text=f"Video: {video_name} | FPS: {self.frame_rate} | Version: {self.video.program_version}"
        )

        if not self.background_thread.is_alive():
            self.background_thread.start()
        else:
            self._buffer_reset()
            print("INFO: Thread already running.")

        # Notes
        self.video.notes = {}
        self.video.dataNotes_path_to_csv = os.path.join(data_dir, f"{video_name}_notes.csv")
        if os.path.exists(self.video.dataNotes_path_to_csv):
            with open(self.video.dataNotes_path_to_csv, mode='r', newline='') as csv_file:
                reader = csv.reader(csv_file)
                next(reader, None)
                for row in reader:
                    if len(row) == 2:
                        frame = int(row[0]); note = row[1]
                        self.video.notes[frame] = note
            print("INFO: Notes loaded successfully.")
        self.update_note_entry()

        # Limb parameters
        if not self.is_pose_mode():
            p1, p2, p3 = load_limb_parameters(os.path.join(data_dir, f"{video_name}_limb_parameters.csv"))
            self.video.limb_parameter1, self.video.limb_parameter2, self.video.limb_parameter3 = p1, p2, p3

        # Clothes file presence => colorize button
        self.video.clothes_file_path = os.path.join(data_dir, f"{video_name}_clothes.txt")
        if (not self.is_pose_mode()) and self.video.clothes_file_path and os.path.exists(self.video.clothes_file_path):
            with open(self.video.clothes_file_path, 'r') as f:
                if len(f.readlines()) > 1:
                    self.cloth_btn.config(bg="lightgreen")

        self.load_video_btn.config(state=tk.DISABLED, bg="gray", fg='lightgray')
        for b in self.video.frames.values():
            if isinstance(b, dict):
                b["Changed"] = False
        self.rebuild_annotation_controls()
        self._set_sort_analysis_state()
        print("INFO: Welcome back! I wish you happy labeling session! :)")

    # === Clothes Side Window ===================================================
    def open_cloth_app(self):
        if self.is_pose_mode():
            print("INFO: Clothes labeling is disabled in 3D mismatch mode.")
            return
        if self.video is None:
            print("ERROR: First select video")
        else:
            if self._cloth_app and self._cloth_app.top_level.winfo_exists():
                self._cloth_app.top_level.lift()
                self._cloth_app.top_level.focus_force()
                return
            file_path = self.video.clothes_file_path
            if not file_path and self.video_name:
                data_dir = os.path.join("Labeled_data", self.video_name, "data")
                file_path = os.path.join(data_dir, f"{self.video_name}_clothes.txt")
            scale = self.clothes_diagram_scale or DEFAULT_CLOTH_DIAGRAM_SCALE
            initial_points = self._load_clothes_points_from_file(file_path, scale)
            self.cloth_btn.config(state=tk.DISABLED)

            def on_save(dots, diagram_scale=None):
                self.update_data_clothes(dots, diagram_scale)

            def on_close(dots, diagram_scale=None):
                self.update_data_clothes(dots, diagram_scale)
                self.cloth_btn.config(state=tk.NORMAL)
                self._cloth_app = None

            try:
                self._cloth_app = ClothApp(
                    self,
                    on_save,
                    on_close,
                    initial_points=initial_points,
                    diagram_scale=scale,
                )
            except Exception as e:
                self.cloth_btn.config(state=tk.NORMAL)
                self._cloth_app = None
                print(f"ERROR: Failed to open Clothes App: {e}")

    def _load_clothes_points_from_file(self, file_path, display_scale):
        if not file_path or not os.path.exists(file_path):
            return []
        file_scale = None
        points = []
        with open(file_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("diagramscale:"):
                    try:
                        file_scale = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        file_scale = None
                    continue
                if "X=" in line and "Y=" in line:
                    match = re.search(r"X=([-\d.]+),\s*Y=([-\d.]+)", line)
                    if match:
                        x = float(match.group(1))
                        y = float(match.group(2))
                        points.append((x, y))
        if file_scale is None:
            file_scale = 0.5
        if file_scale <= 0:
            file_scale = display_scale or DEFAULT_CLOTH_DIAGRAM_SCALE
        display_scale = display_scale or DEFAULT_CLOTH_DIAGRAM_SCALE
        scale_ratio = display_scale / file_scale
        return [(x * scale_ratio, y * scale_ratio) for x, y in points]

    def update_data_clothes(self, dots, diagram_scale=None):
        self.data_clothes = dots
        if diagram_scale:
            self.clothes_diagram_scale = float(diagram_scale)
        print("Data clothes updated:", self.data_clothes)
        self.save_clothes_to_text()
        self.cloth_btn.config(bg="lightgreen")

    def save_clothes_to_text(self):
        print("INFO: Saving clothes...")
        if not self.video.dataRH_path_to_csv:
            print("ERROR: Data path is not set"); return
        data_folder = os.path.dirname(self.video.dataRH_path_to_csv)
        os.makedirs(data_folder, exist_ok=True)
        text_file_path = os.path.join(data_folder, f"{self.video_name}_clothes.txt")
        self.video.clothes_file_path = text_file_path
        scale = self.clothes_diagram_scale or DEFAULT_CLOTH_DIAGRAM_SCALE
        with open(text_file_path, mode='w') as f:
            f.write("Coordinates and Zones for Clothing Items:\n")
            f.write(f"DiagramScale: {scale}\n")
            for dot_id, (x, y) in self.data_clothes.items():
                if scale == 0:
                    scale = DEFAULT_CLOTH_DIAGRAM_SCALE
                zones = self.find_image_with_white_pixel(x / scale, y / scale)
                zones_str = ','.join(zones)
                f.write(f"Dot ID {dot_id}: X={x}, Y={y}, Zones={zones_str}\n")
        print("INFO: Clothes saved")

    

    # === App Lifecycle (close, position) =======================================
    def on_close(self):
        saved = False
        if self.video is not None:
            self.save_data()
            self.save_last_position()
            self._finalize_video_time()
            saved = True
        try:
            self._loader_pool.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            print(f"WARN: loader pool shutdown failed: {e}")
        custom_confirm_close(self, saved)

    def _last_position_path(self, data_dir: str, video_name: str) -> str:
        return os.path.join(data_dir, f"{video_name}_last_position.json")

    def save_last_position(self):
        if self.video is None or self.video_name is None:
            return
        data_dir = os.path.join("Labeled_data", self.video_name, "data")
        os.makedirs(data_dir, exist_ok=True)
        path = self._last_position_path(data_dir, self.video_name)
        try:
            payload = {
                "frame": int(self.video.current_frame),
                "total_frames": int(self.video.total_frames),
            }
            with open(path, "w") as f:
                json.dump(payload, f)
            print(f"INFO: Saved last position → {path}")
        except Exception as e:
            print(f"WARNING: Failed to save last position: {e}")

    def restore_last_position(self, data_dir: str, video_name: str):
        path = self._last_position_path(data_dir, video_name)
        if not os.path.exists(path):
            return
        try:
            with open(path, "r") as f:
                payload = json.load(f) or {}
            frame = int(payload.get("frame", 0))
            frame = max(0, min(self.video.total_frames, frame))
            self.video.current_frame = frame
            self.video.current_frame_zone = int(self.video.current_frame / self.video.number_frames_in_zone)
            print(f"INFO: Restored last position: frame {self.video.current_frame}")
        except Exception as e:
            print(f"WARNING: Failed to restore last position: {e}")

    # === Settings ==============================================================
    def open_settings(self):
        if getattr(self, "_settings_win", None) and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return

        cfg = load_config()
        win = tk.Toplevel(self)
        win.title("Settings")
        win.resizable(False, False)
        self._settings_win = win

        def _v(key, default):
            return cfg.get(key, default)

        vars_map = {
            "video_downscale": tk.StringVar(value=str(_v("video_downscale", 1.0))),
            "diagram_scale": tk.StringVar(value=str(_v("diagram_scale", 1.0))),
            "dot_size": tk.StringVar(value=str(_v("dot_size", 10))),
            "jump_seconds": tk.StringVar(value=str(_v("jump_seconds", 1.0))),
            "parameter1": tk.StringVar(value=str(_v("parameter1", "Parameter 1"))),
            "parameter2": tk.StringVar(value=str(_v("parameter2", "Parameter 2"))),
            "parameter3": tk.StringVar(value=str(_v("parameter3", "Parameter 3"))),
            "limb_parameter1": tk.StringVar(value=str(_v("limb_parameter1", "Limb Parameter 1"))),
            "limb_parameter2": tk.StringVar(value=str(_v("limb_parameter2", "Limb Parameter 2"))),
            "limb_parameter3": tk.StringVar(value=str(_v("limb_parameter3", "Limb Parameter 3"))),
        }

        row = 0
        tk.Label(win, text="Display").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(10, 4))
        row += 1
        tk.Label(win, text="Video downscale (1 = full, 2 = half)").grid(
            row=row, column=0, sticky="w", padx=8, pady=2
        )
        tk.Entry(win, textvariable=vars_map["video_downscale"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Diagram scale").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["diagram_scale"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Dot size").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["dot_size"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Fast-jump seconds (>> / Shift+Arrow)").grid(
            row=row, column=0, sticky="w", padx=8, pady=2
        )
        tk.Entry(win, textvariable=vars_map["jump_seconds"], width=10).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1

        def parse_float(value, key):
            if value is None:
                return 0.0
            s = str(value).strip()
            if s == "":
                return 0.0
            try:
                return float(s)
            except Exception:
                raise ValueError(f"{key} must be a number")

        tk.Label(win, text="Parameter Labels").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(10, 4))
        row += 1
        tk.Label(win, text="Limb Parameter 1").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["limb_parameter1"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Limb Parameter 2").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["limb_parameter2"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Limb Parameter 3").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["limb_parameter3"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Parameter 1").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["parameter1"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Parameter 2").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["parameter2"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1
        tk.Label(win, text="Parameter 3").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tk.Entry(win, textvariable=vars_map["parameter3"], width=18).grid(
            row=row, column=1, sticky="w", padx=8, pady=2
        )
        row += 1

        def apply_settings(close=False):
            try:
                new_cfg = dict(cfg)
                downscale = parse_float(vars_map["video_downscale"].get(), "video_downscale")
                if downscale <= 0:
                    downscale = 1.0
                new_cfg["video_downscale"] = downscale
                new_cfg["diagram_scale"] = parse_float(vars_map["diagram_scale"].get(), "diagram_scale")
                new_cfg["dot_size"] = parse_float(vars_map["dot_size"].get(), "dot_size")
                jump_seconds = parse_float(vars_map["jump_seconds"].get(), "jump_seconds")
                if jump_seconds <= 0:
                    jump_seconds = 1.0
                new_cfg["jump_seconds"] = jump_seconds

                def _label_from(key, default):
                    raw = vars_map[key].get()
                    val = str(raw).strip() if raw is not None else ""
                    return val if val else default

                new_cfg["parameter1"] = _label_from("parameter1", new_cfg.get("parameter1", "Parameter 1"))
                new_cfg["parameter2"] = _label_from("parameter2", new_cfg.get("parameter2", "Parameter 2"))
                new_cfg["parameter3"] = _label_from("parameter3", new_cfg.get("parameter3", "Parameter 3"))
                new_cfg["limb_parameter1"] = _label_from("limb_parameter1", new_cfg.get("limb_parameter1", "Limb Parameter 1"))
                new_cfg["limb_parameter2"] = _label_from("limb_parameter2", new_cfg.get("limb_parameter2", "Limb Parameter 2"))
                new_cfg["limb_parameter3"] = _label_from("limb_parameter3", new_cfg.get("limb_parameter3", "Limb Parameter 3"))
            except ValueError as e:
                messagebox.showerror("Invalid settings", str(e), parent=win)
                return

            save_config(new_cfg)
            self.apply_runtime_settings(new_cfg)
            if close:
                win.destroy()

        btn_frame = tk.Frame(win)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(10, 8))
        tk.Button(btn_frame, text="Apply", command=lambda: apply_settings(close=False)).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Apply & Close", command=lambda: apply_settings(close=True)).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Close", command=win.destroy).pack(side="left", padx=5)

    def apply_runtime_settings(self, cfg: dict):
        self.perf.enabled = bool(cfg.get("perf_enabled", False))
        self.perf.log_every_s = float(cfg.get("perf_log_every_s", 2.0))
        self.perf.top_n = int(cfg.get("perf_log_top_n", 6))

        new_downscale = float(cfg.get("video_downscale", 1.0))
        if new_downscale <= 0:
            new_downscale = 1.0
        self.video_downscale = new_downscale

        new_jump_seconds = float(cfg.get("jump_seconds", 1.0))
        if new_jump_seconds <= 0:
            new_jump_seconds = 1.0
        self.jump_seconds = new_jump_seconds
        if self.video is not None and getattr(self, "frame_rate", None):
            self.jump_frame_count = max(1, round(self.frame_rate * self.jump_seconds))
            print(f"INFO: Fast-jump updated to {self.jump_frame_count} frames "
                  f"({self.jump_seconds}s @ {self.frame_rate} fps)")
        else:
            print(f"INFO: Fast-jump updated to {self.jump_seconds}s (no video loaded)")
        self._refresh_jump_label()

        new_scale = float(cfg.get("diagram_scale", 1.0))
        new_dot = float(cfg.get("dot_size", 10))
        self.diagram_scale = new_scale
        self.dot_size = new_dot

        # Refresh parameter labels on buttons (and on the active video if present).
        try:
            target = self.video if self.video is not None else self
            load_parameter_names_into(
                target,
                {1: self.par1_btn, 2: self.par2_btn, 3: self.par3_btn},
                {1: self.limb_par1_btn, 2: self.limb_par2_btn, 3: self.limb_par3_btn},
            )
        except Exception:
            pass

        base_w, base_h = 450, 696
        w, h = int(base_w * new_scale), int(base_h * new_scale)
        try:
            self.diagram_canvas.config(width=w, height=h)
            self.diagram_canvas.delete("all")
            self.on_radio_click()
        except Exception:
            pass

        # Flush buffer so new resolution takes effect immediately.
        if hasattr(self, "img_buffer"):
            self._buffer_reset()
        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._timeline_playhead_id = None
        self._timeline2_playhead_id = None
        self._pose_timeline_scale_overlay_id = None
        self._pose_timeline2_scale_overlay_id = None
        if getattr(self, "video", None):
            self.display_first_frame()

    # === Frame Stepping ========================================================
    def next_frame(self, number_of_frames, play=False):
        if self.video is None:
            print("ERROR: Video = None"); return
        if number_of_frames > 0:
            self.video.current_frame = min(self.video.total_frames, self.video.current_frame + number_of_frames)
            self._last_step_sign = 1
        elif number_of_frames < 0:
            self.video.current_frame = max(0, self.video.current_frame + number_of_frames)
            self._last_step_sign = -1
        else:
            print("ERROR: Wrong number of frames."); return

        # Wake the buffering thread if the destination isn't cached so it gets
        # loaded with priority before any prefetch fills.
        if self.video.current_frame not in self.img_buffer:
            self._priority_event.set()

        self.display_first_frame()
        if not play: self.draw_timeline()
        self.draw_timeline2()
