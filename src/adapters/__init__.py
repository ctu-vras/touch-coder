"""Adapters layer: everything that touches the outside world.

File I/O (unified journal, export CSV, config.json), cv2 probing/decoding,
ffmpeg frame extraction. Adapters may import from `domain`, never from `gui`
widgets (the `resource_utils` path helper is the one sanctioned exception).
"""
