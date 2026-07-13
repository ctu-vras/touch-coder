# Export Notes for External Pipeline Consumers

## 2026-07-13 — Per-limb `Look` retired

Per-limb gaze (`Look`) has been fully retired from TinyTouch. Current-format exports never
contained `{limb}_Look` columns, so this does not change the current export schema.

External scripts that still reference `Look` columns from legacy exports should use the
global `Parameter_1` column instead. This is where gaze is captured; its user-facing label
(currently `Looking1`) is recorded under `Param Labels` in the export metadata sidecar.
