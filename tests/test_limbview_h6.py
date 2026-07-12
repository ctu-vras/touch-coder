"""
H6 regression guards for `video_model.LimbView`.

Black-box against the in-memory data model (no cv2 / Tk). Verifies that:
  (a) reading an unlabeled frame through a limb view never inserts into the
      backing `frames` dict (no create-on-read), and
  (b) len() / `in` / iteration reflect the REAL backing content instead of a
      dead, always-empty UserDict store.

The view is constructed with a `dict` subclass that ALSO exposes itself as
`.frames`, so the same test file is signature-agnostic: it is RED against the
HEAD ctor `LimbView(frames, limb)` and GREEN against the fixed ctor
`LimbView(video, limb)`.
"""
from video_model import LimbView
from data_utils import empty_bundle


class _Owner(dict):
    """Doubles as the frames dict (HEAD's ctor) AND as the Video owner (fixed ctor)."""
    @property
    def frames(self):
        return self


def _labeled_frame(limb):
    b = empty_bundle()
    b[limb]["Onset"] = "ON"
    b["Changed"] = True
    return b


def test_H6_read_does_not_insert():
    owner = _Owner({2: _labeled_frame("RH")})
    view = LimbView(owner, "RH")
    before = len(owner)
    _ = view.get(999)                  # non-mutating read of an unlabeled frame
    assert len(owner) == before        # .get() must never insert
    try:
        _ = view[999]                  # HEAD: setdefault inserts -> len grows; FIXED: KeyError
    except KeyError:
        pass
    assert len(owner) == before        # create-on-read must not leave an empty bundle behind


def test_H6_len_in_iter_reflect_content():
    owner = _Owner({2: _labeled_frame("RH"), 5: _labeled_frame("RH")})
    view = LimbView(owner, "RH")
    assert len(view) == 2              # RED at HEAD: UserDict.__len__ -> 0
    assert 2 in view and 999 not in view   # RED at HEAD: __contains__ -> always False
    assert sorted(view) == [2, 5]      # RED at HEAD: __iter__ -> empty


def test_H6_write_creates_bundle_and_sets_only_that_limb():
    owner = _Owner()
    view = LimbView(owner, "LH")
    view[3] = {"X": [1], "Y": [2], "Onset": "ON", "Bodypart": "LH",
               "Look": "No", "Zones": [["FACE"]], "Touch": None}
    assert 3 in owner and owner[3]["LH"]["Onset"] == "ON"
    assert owner[3]["RH"] == empty_bundle()["RH"]   # other limbs untouched
