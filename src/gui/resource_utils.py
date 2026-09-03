"""
gui/resource_utils.py
Path resolution for the two kinds of files the app reads that are NOT user data.

  * `resource_path`  — files that sit at the ROOT of the distribution
    (`config.json`). From source that root is the repo root; frozen it is the
    PyInstaller extraction dir (`sys._MEIPASS`).
  * `asset_path`     — runtime assets shipped inside the package under
    `src/resources/` (icons, diagrams, zone masks). Frozen, TinyTouch.spec
    bundles the whole `src/resources` tree as `resources/` at the top of
    `_MEIPASS`, so the relative path below the resources root is identical in
    both modes. If you change the spec's `datas` destination, change
    `_resources_root` with it — nothing else knows the layout.

`get_app_dir` is different again: it is the WRITABLE install dir next to the
executable (where the user's own config.json lives), never inside _MEIPASS.
"""

import os
import sys

RESOURCES_SUBDIR = "resources"


def get_repo_root() -> str:
    # This file lives at src/gui/resource_utils.py — the repo root is two
    # levels up (was one level up before the gui/ package move).
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def get_src_root() -> str:
    """The `src/` package root (one level up from `src/gui/`)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return get_repo_root()


def _bundle_root() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return get_repo_root()


def _resources_root() -> str:
    """Root of the bundled runtime-asset tree.

    Frozen: `<_MEIPASS>/resources` (spec datas: src/resources -> "resources").
    From source: `src/resources`.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, RESOURCES_SUBDIR)
    return os.path.join(get_src_root(), RESOURCES_SUBDIR)


def resource_path(relative_path: str) -> str:
    """Path to a distribution-root file, e.g. resource_path("config.json")."""
    return os.path.join(_bundle_root(), relative_path)


def asset_path(relative_path: str) -> str:
    """Path to a bundled runtime asset, e.g. asset_path("icons/diagram.png")."""
    return os.path.join(_resources_root(), relative_path)
