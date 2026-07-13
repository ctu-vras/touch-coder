# Code review progress overview

**Review date:** 2026-07-12  
**Last updated:** 2026-07-13  
**Source:** [Full code review](review.md)

This page tracks whether each review finding has an implementation plan and whether the fix is complete. A finding is considered fixed when its plan is in [done/](done/); plans in [to_do/](to_do/) are ready but not yet implemented.

## At a glance

| Metric | Progress |
|---|---:|
| Actionable findings fixed | 🟢 **11 / 33 (33%)** |
| Actionable findings still open | **22 / 33 (67%)** |
| Findings with a written plan | **20 / 33 (61%)** |
| Open findings with a plan | 🟣 **9** |
| Open findings without a plan | ⚪ **13** |
| Superseded findings | ⚫ **1** |

| Severity | Total | 🟢 Fixed | 🟣 Planned | ⚪ No plan | ⚫ Superseded |
|---|---:|---:|---:|---:|---:|
| 🔴 Critical | 4 | 3 | 0 | 0 | 1 |
| 🟠 High | 6 | 6 | 0 | 0 | 0 |
| 🟡 Medium | 12 | 2 | 9 | 1 | 0 |
| 🔵 Low / cleanup | 12 | 0 | 0 | 12 | 0 |
| **Total** | **34** | **11** | **9** | **13** | **1** |

## Legend

- **Severity:** 🔴 Critical · 🟠 High · 🟡 Medium · 🔵 Low / cleanup
- **Progress:** 🟢 Fixed · 🟣 Plan ready, not fixed · ⚪ No plan, not fixed · ⚫ Superseded / no fix required
- **Plan:** ✅ A plan document exists · ❌ No plan document exists · ➖ A plan is not required

## Findings

| ID | Finding | Severity | Plan | Progress |
|---|---|---|---|---|
| C1 | No atomic writes | 🔴 Critical | [✅ Plan](done/fix_C1.md) | 🟢 Fixed |
| C2 | Global key bindings leak into the Note entry | 🔴 Critical | [✅ Plan](done/fix_C2.md) | 🟢 Fixed |
| C3 | Sort Frames is broken | 🔴 Critical | ➖ Superseded | ⚫ No fix required; feature is being removed |
| C4 | Pose save can discard all prior rows after a read error | 🔴 Critical | [✅ Plan](done/fix_C4.md) | 🟢 Fixed |
| H1 | Tkinter is called from background threads | 🟠 High | [✅ Plan](done/fix_H1.md) | 🟢 Fixed |
| H2 | Per-edit O(N) timeline rebuild | 🟠 High | [✅ Plan](done/fix_H2.md) | 🟢 Fixed |
| H3 | Incremental saves perform a full read and rewrite | 🟠 High | [✅ Plan](done/fix_H3.md) | 🟢 Fixed |
| H4 | Config loaders crash on corrupt JSON | 🟠 High | [✅ Plan](done/fix_H4.md) | 🟢 Fixed |
| H5 | OpenCV extraction fallback has no failure signal | 🟠 High | [✅ Plan](done/fix_H5.md) | 🟢 Fixed |
| H6 | `LimbView` mutates on read and has a dead backing store | 🟠 High | [✅ Plan](done/fix_H6.md) | 🟢 Fixed |
| M1 | Limb parameter stores the string `"None"` | 🟡 Medium | [✅ Plan](to_do/fix_M1.md) | 🟣 Not fixed |
| M2 | `mark_bundle_changed(index)` ignores its argument | 🟡 Medium | [✅ Plan](to_do/fix_M2.md) | 🟣 Not fixed |
| M3 | `_Look` / gaze data is never exported | 🟡 Medium | [✅ Plan](to_do/fix_M3.md) | 🟣 Not fixed |
| M4 | Division by zero when FPS is 0 | 🟡 Medium | [✅ Plan](done/fix_M4.md) | 🟢 Fixed |
| M5 | Left/right string swap can corrupt free text | 🟡 Medium | [✅ Plan](to_do/fix_M5.md) | 🟣 Not fixed |
| M6 | Missing UTF-8 encoding on text file operations | 🟡 Medium | [✅ Plan](to_do/fix_M6.md) | 🟣 Not fixed |
| M7 | Resource leaks and O(n²) frame-extraction polling | 🟡 Medium | ❌ No plan | ⚪ Not fixed |
| M8 | Pose loader uses slow `iterrows()` | 🟡 Medium | [✅ Plan](to_do/fix_M8.md) | 🟣 Not fixed |
| M9 | Canceling close leaves the app half shut down | 🟡 Medium | [✅ Plan](done/fix_M9.md) | 🟢 Fixed |
| M10 | `parse_xy` can desynchronize X/Y/Zones | 🟡 Medium | [✅ Plan](to_do/fix_M10.md) | 🟣 Not fixed |
| M11 | Pose `ScaleFactor` is not clamped on load | 🟡 Medium | [✅ Plan](to_do/fix_M11.md) | 🟣 Not fixed |
| M12 | Analysis hides read errors and loses transition data | 🟡 Medium | [✅ Plan](to_do/fix_M12.md) | 🟣 Not fixed |
| L1 | Global mouse bindings affect the Clothes dialog | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L2 | `d` shortcut uses stale mouse coordinates | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L3 | Note save synthesizes a global Tab keypress | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L4 | Pose auto-carry fields are not persisted | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L5 | Pose schema documentation has drifted | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L6 | Pose rendering performance timing is mis-indented | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L7 | Image processing uses slow per-pixel Python loops | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L8 | Exceptions are silently swallowed | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L9 | Duplicate imports and scattered local imports | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L10 | Program version is hardcoded in three branches | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L11 | Malformed CSV rows can crash data loading | 🔵 Low | ❌ No plan | ⚪ Not fixed |
| L12 | Zone names rely on fragile hardcoded indexes | 🔵 Low | ❌ No plan | ⚪ Not fixed |

## Maintenance rule

When work starts, add a `fix_<ID>.md` plan under `to_do/`. When the implementation and its verification are complete, move that plan to `done/`, change the row to **🟢 Fixed**, and update the counts above. Keep findings that will not be implemented marked **⚫ Superseded** with the reason.
