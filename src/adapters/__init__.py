"""Adapters layer: everything that touches the outside world.

The working-state SQLite database (`sqlite_repo`), file I/O (export CSV read
and write, config.json), cv2 probing/decoding, ffmpeg frame extraction. Adapters may import from `domain`, never from `gui`
widgets (the `resource_utils` path helper is the one sanctioned exception).
"""
