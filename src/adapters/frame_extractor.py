"""
adapters/frame_extractor.py
Frame folder management and generation (was frame_utils.py).
"""

import logging
import os
import re
import time
import shutil
import subprocess
import threading

import cv2

from domain.project import ProjectPaths


logger = logging.getLogger(__name__)


_FRAME_RE = re.compile(r"^frame(\d+)\.(jpg|jpeg|png)$", re.IGNORECASE)
FRAME_COUNT_TOLERANCE_PCT = 0.001  # allow up to 0.1% missing frames


class FrameExtractionError(RuntimeError):
    """Raised when frame extraction or reliability copy cannot complete safely."""


class FrameExtractionCancelled(FrameExtractionError):
    """Raised when the caller cancels an in-progress frame extraction."""


def _raise_if_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise FrameExtractionCancelled("Frame extraction cancelled.")


def check_items_count(folder_path, expected_count):
    items = os.listdir(folder_path) if os.path.exists(folder_path) else []
    total_items = len(items)
    frame_indices = []
    non_frame_items = []

    for name in items:
        match = _FRAME_RE.match(name)
        if match:
            frame_indices.append(int(match.group(1)))
        else:
            non_frame_items.append(name)

    frame_count = len(frame_indices)
    expected_files = (expected_count + 1) if expected_count is not None and expected_count >= 0 else expected_count

    logger.debug("Frames dir exists: %s", os.path.exists(folder_path))
    logger.debug("Number of files in frames folder: %d", total_items)
    logger.debug("Frame files detected: %d", frame_count)
    if expected_files is not None:
        logger.debug("Expected frame files: %d", expected_files)
        logger.debug("Expected last frame index: %s", expected_count)
        allowed_missing = max(1, int(expected_files * FRAME_COUNT_TOLERANCE_PCT)) if expected_files > 0 else 0
        logger.debug(
            "Frame count tolerance: %.3f%% (allow %d missing frames)",
            FRAME_COUNT_TOLERANCE_PCT * 100,
            allowed_missing,
        )
    else:
        logger.warning("Expected frame count is undefined.")

    if non_frame_items:
        sample = ", ".join(non_frame_items[:5])
        logger.warning(
            "Non-frame items in folder: %d (sample: %s)", len(non_frame_items), sample
        )

    if frame_indices:
        min_idx = min(frame_indices)
        max_idx = max(frame_indices)
        logger.debug("Frame index range in folder: %d..%d", min_idx, max_idx)
        if expected_count is not None and expected_count >= 0 and max_idx != expected_count:
            logger.warning("Max frame index does not match expected last index.")

    if expected_files is None:
        return False
    allowed_missing = max(1, int(expected_files * FRAME_COUNT_TOLERANCE_PCT)) if expected_files > 0 else 0
    min_ok = expected_files - allowed_missing
    if frame_count < min_ok or frame_count > expected_files:
        logger.warning(
            "Frame file count mismatch: expected %d, found %d", expected_files, frame_count
        )
        return False
    if frame_count != expected_files:
        logger.warning(
            "Frame file count within tolerance: expected %d, found %d",
            expected_files,
            frame_count,
        )
    return True


def _get_ffmpeg_exe():
    """Return path to bundled ffmpeg binary, or None if unavailable."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except ImportError:
        pass
    return None


def _count_jpg_files(frames_dir):
    try:
        return sum(1 for f in os.listdir(frames_dir) if f.endswith(".jpg"))
    except FileNotFoundError:
        return 0


def _advance_sequential_count(frames_dir, count):
    """Advance through ffmpeg's sequential frameN.jpg output without rescanning."""
    while os.path.exists(os.path.join(frames_dir, f"frame{count}.jpg")):
        count += 1
    return count


def _extract_frames_ffmpeg(
    video_path,
    frames_dir,
    progress_cb,
    progress_interval_s,
    cancel_event=None,
):
    """Extract frames using the bundled ffmpeg binary (fast path)."""
    ffmpeg_exe = _get_ffmpeg_exe()
    if ffmpeg_exe is None:
        logger.debug("ffmpeg binary not available")
        return False

    logger.info("Using ffmpeg for frame extraction: %s", ffmpeg_exe)

    os.makedirs(frames_dir, exist_ok=True)
    output_pattern = os.path.join(frames_dir, "frame%d.jpg")

    cap = cv2.VideoCapture(video_path)
    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    logger.debug(
        "Video properties: frames=%d, fps=%.3f, size=%dx%d",
        total_frames,
        fps,
        width,
        height,
    )

    # -nostats -loglevel error: keeps stderr almost silent, so the OS pipe
    # buffer can't fill up and deadlock ffmpeg while we poll for progress.
    cmd = [
        ffmpeg_exe,
        "-nostats",
        "-loglevel", "error",
        "-i", video_path,
        "-q:v", "2",
        "-start_number", "0",
        output_pattern,
    ]

    start_time = time.time()
    last_progress_ts = 0.0

    logger.debug("Spawning ffmpeg: %s", " ".join(cmd))
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    logger.debug("ffmpeg started (pid=%d)", process.pid)

    # Defense-in-depth: drain stdout/stderr concurrently so the pipes can
    # never block ffmpeg even if a future change makes it chatty again.
    stdout_chunks = []
    stderr_chunks = []

    def _drain(stream, sink):
        try:
            for chunk in iter(lambda: stream.read(4096), b""):
                if not chunk:
                    break
                sink.append(chunk)
        except Exception as exc:
            logger.warning("pipe drainer error: %s", exc)

    stdout_thread = None
    stderr_thread = None
    started_threads = []
    count = 0

    def _join_drainers(timeout):
        for thread in started_threads:
            try:
                thread.join(timeout=timeout)
            except Exception as exc:
                logger.warning("failed joining ffmpeg pipe drainer: %r", exc)

    try:
        stdout_thread = threading.Thread(
            target=_drain, args=(process.stdout, stdout_chunks), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_drain, args=(process.stderr, stderr_chunks), daemon=True
        )
        stdout_thread.start()
        started_threads.append(stdout_thread)
        stderr_thread.start()
        started_threads.append(stderr_thread)

        while process.poll() is None:
            _raise_if_cancelled(cancel_event)
            time.sleep(progress_interval_s)
            _raise_if_cancelled(cancel_event)
            if progress_cb and total_frames:
                now = time.time()
                if (now - last_progress_ts) >= progress_interval_s:
                    last_progress_ts = now
                    count = _advance_sequential_count(frames_dir, count)
                    progress_cb(count, total_frames, "Generating frames", now - start_time)
                    # The callback pumps Tk events. Closing the application can
                    # therefore set cancellation before the callback returns.
                    _raise_if_cancelled(cancel_event)

        _raise_if_cancelled(cancel_event)
        _join_drainers(timeout=5.0)

        rc = process.returncode
        duration = time.time() - start_time
        logger.debug("ffmpeg exited rc=%s after %.1fs", rc, duration)

        if rc != 0:
            stderr = b"".join(stderr_chunks).decode(errors="replace")
            logger.warning("ffmpeg failed (exit %s): %s", rc, stderr[-1000:])
            return False

        logger.debug("Counting extracted frame files")
        listdir_start = time.time()
        frame_count = _count_jpg_files(frames_dir)
        logger.info(
            "ffmpeg extracted %d frames (file count took %.1fs)",
            frame_count,
            time.time() - listdir_start,
        )

        # Final progress report.
        if progress_cb and total_frames:
            progress_cb(frame_count, total_frames, "Generating frames", time.time() - start_time)
            _raise_if_cancelled(cancel_event)

        if total_frames and abs(frame_count - total_frames) > max(1, int(total_frames * FRAME_COUNT_TOLERANCE_PCT)):
            logger.warning(
                "ffmpeg generated %d frames, but expected %d.", frame_count, total_frames
            )

        return True
    finally:
        try:
            still_running = process.poll() is None
        except Exception as exc:
            still_running = True
            logger.warning(
                "could not poll ffmpeg during cleanup (pid=%d): %r", process.pid, exc
            )
        if still_running:
            logger.debug(
                "terminating ffmpeg (pid=%d, video=%r) before extraction completed",
                process.pid,
                video_path,
            )
            try:
                process.kill()
            except Exception as exc:
                logger.warning("failed to kill ffmpeg (pid=%d): %r", process.pid, exc)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("timed out waiting for ffmpeg (pid=%d) to exit", process.pid)
            except Exception as exc:
                logger.warning("failed waiting for ffmpeg (pid=%d): %r", process.pid, exc)
        _join_drainers(timeout=5.0)
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception as exc:
                logger.warning("failed closing ffmpeg pipe (pid=%d): %r", process.pid, exc)
        for thread in started_threads:
            if thread.is_alive():
                try:
                    thread.join(timeout=1.0)
                except Exception as exc:
                    logger.warning("failed joining ffmpeg pipe drainer after close: %r", exc)


def _extract_frames_opencv(
    video_path,
    frames_dir,
    progress_cb,
    progress_interval_s,
    cancel_event=None,
):
    """Extract frames using OpenCV sequential decode+write (fallback path)."""
    logger.debug("Using OpenCV for frame extraction (fallback)")

    vidcap = cv2.VideoCapture(video_path)
    try:
        is_opened = vidcap.isOpened()
        total_frames = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = vidcap.get(cv2.CAP_PROP_FPS)
        width = int(vidcap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(vidcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.debug("VideoCapture opened: %s", is_opened)
        logger.debug(
            "Video properties: frames=%d, fps=%.3f, size=%dx%d",
            total_frames,
            fps,
            width,
            height,
        )

        success, image = vidcap.read()
        count = 0
        os.makedirs(frames_dir, exist_ok=True)

        last_progress_ts = 0.0
        start_time = time.time()

        while success:
            _raise_if_cancelled(cancel_event)
            frame_path = os.path.join(frames_dir, f"frame{count}.jpg")
            if not cv2.imwrite(frame_path, image):
                msg = (
                    f"cv2.imwrite failed at frame {count} -> {frame_path} "
                    "(disk full / unwritable?)"
                )
                logger.error("%s", msg)
                raise FrameExtractionError(msg)
            success, image = vidcap.read()
            count += 1
            if progress_cb:
                now = time.time()
                if (now - last_progress_ts) >= progress_interval_s or count >= total_frames:
                    last_progress_ts = now
                    progress_cb(count, total_frames or count, "Generating frames", now - start_time)
                    _raise_if_cancelled(cancel_event)
    finally:
        vidcap.release()
    logger.info("OpenCV extracted %d frames in %.1fs.", count, time.time() - start_time)
    if total_frames and count != total_frames:
        logger.warning(
            "Generated %d frames, but OpenCV reported %d total frames.", count, total_frames
        )
    return count


def create_frames(
    video_path,
    frames_dir,
    labeling_mode,
    video_name,
    progress_cb=None,
    progress_interval_s=1.0,
    original_frames_dir=None,
    cancel_event=None,
):
    logger.debug("Checking if frames need to be created")
    _raise_if_cancelled(cancel_event)

    if labeling_mode == "Reliability":
        if original_frames_dir is None:
            # Reliability projects copy frames from their non-reliability
            # original; derive its location from the canonical project layout.
            original_frames_dir = ProjectPaths(video_name).original.frames_dir
        if os.path.exists(original_frames_dir):
            logger.info(
                "Found existing frames at %s. Copying instead of generating",
                original_frames_dir,
            )
            os.makedirs(frames_dir, exist_ok=True)
            source_items = os.listdir(original_frames_dir)
            frame_files = [filename for filename in source_items if _FRAME_RE.match(filename)]
            skipped_files = [filename for filename in source_items if not _FRAME_RE.match(filename)]
            if skipped_files:
                sample = ", ".join(skipped_files[:5])
                logger.warning(
                    "Reliability copy skipped %d non-frame items (sample: %s)",
                    len(skipped_files),
                    sample,
                )
            total_files = len(frame_files)
            start_time = time.time()
            last_progress_ts = 0.0
            for index, filename in enumerate(frame_files):
                _raise_if_cancelled(cancel_event)
                src = os.path.join(original_frames_dir, filename)
                dst = os.path.join(frames_dir, filename)
                try:
                    shutil.copy2(src, dst)
                except OSError as exc:
                    msg = f"reliability frame copy failed: {src} -> {dst}: {exc!r}"
                    logger.error("%s", msg)
                    raise FrameExtractionError(msg) from exc
                if progress_cb:
                    now = time.time()
                    if (now - last_progress_ts) >= progress_interval_s:
                        last_progress_ts = now
                        progress_cb(index + 1, total_files, "Copying frames", now - start_time)
                        _raise_if_cancelled(cancel_event)
            if progress_cb:
                now = time.time()
                progress_cb(total_files, total_files, "Copying frames", now - start_time)
                _raise_if_cancelled(cancel_event)
            logger.info(
                "Frames copied successfully (%d files in %.1fs).",
                total_files,
                time.time() - start_time,
            )
            if total_files == 0:
                raise FrameExtractionError(
                    f"Reliability copy produced 0 frames from {original_frames_dir}")
            return total_files

    logger.info("Creating frames from video")

    # Try ffmpeg first (faster), fall back to OpenCV.
    try:
        ffmpeg_args = (video_path, frames_dir, progress_cb, progress_interval_s)
        ffmpeg_ok = (
            _extract_frames_ffmpeg(*ffmpeg_args)
            if cancel_event is None
            else _extract_frames_ffmpeg(*ffmpeg_args, cancel_event=cancel_event)
        )
        if not ffmpeg_ok:
            logger.warning("Falling back to OpenCV extraction.")
            opencv_args = (video_path, frames_dir, progress_cb, progress_interval_s)
            if cancel_event is None:
                _extract_frames_opencv(*opencv_args)
            else:
                _extract_frames_opencv(*opencv_args, cancel_event=cancel_event)
    except FrameExtractionError:
        raise
    except Exception as exc:
        msg = f"Unexpected failure while extracting frames from {video_path!r}: {exc!r}"
        logger.exception("%s", msg)
        raise FrameExtractionError(msg) from exc

    # Recount after extraction so the guard covers BOTH the ffmpeg and OpenCV
    # paths uniformly. A genuinely empty folder is a hard failure (rule 0:
    # never fail silently); a non-zero-but-below-tolerance result stays a WARN.
    _raise_if_cancelled(cancel_event)
    frame_count = _count_jpg_files(frames_dir)
    if frame_count == 0:
        msg = (f"Frame extraction produced 0 frames for {video_path!r}. "
               f"The file may be unreadable or use an unsupported codec.")
        logger.error("%s", msg)
        raise FrameExtractionError(msg)
    logger.info("create_frames() finished (%d frames).", frame_count)
    return frame_count
