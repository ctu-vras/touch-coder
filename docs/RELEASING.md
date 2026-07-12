# Releasing a new TinyTouch version

This is the operational checklist for cutting a new release. The actual work is done by the GitHub Actions workflow [.github/workflows/build.yml](../.github/workflows/build.yml) — your job is to bump the version, tag, push, and verify.

## TL;DR

```bash
# 1. From a clean master with everything you want shipped already merged:
git status                       # working tree must be clean
git pull --ff-only origin master

# 2. Bump the version string in src/video_model.py (see "Bump version" below).
#    Commit it. Push.
git add src/video_model.py
git commit -m "Bump version to 7.7.0"
git push origin master

# 3. Tag the commit and push the tag — this triggers the build + release.
git tag v7.7.0
git push origin v7.7.0

# 4. Watch the build at:
#    https://github.com/ctu-vras/touch-coder/actions
#    Three jobs run in parallel: Windows x64, Linux x64, Linux Legacy x64.
#    Once all three succeed, a GitHub Release named "TinyTouch v7.7.0"
#    appears with the three zips attached.
```

If you break something, see [Rolling back a bad release](#rolling-back-a-bad-release).

## Pre-release checklist

Run through this before tagging. None of these are enforced by CI — they're soft rules to keep the release sane.

- [ ] You're on `master` and it's up to date with `origin/master`.
- [ ] Working tree is clean (`git status` shows nothing modified, nothing staged).
- [ ] All work that should ship is **merged into master**, not lingering on a branch.
- [ ] `python src/main.py` runs locally (smoke test the GUI, load a video, save once).
- [ ] `pyinstaller TinyTouch.spec` succeeds locally if you changed anything that affects packaging (new dependency, new asset under `icons/`, anything imported via PyInstaller hooks). Skip if it's a pure code change.
- [ ] Decide the new version using semver (see [Versioning](#versioning)).
- [ ] **Bump the version string** in [src/video_model.py](../src/video_model.py) (see below).

## Bump version

The `program_version` string is **hardcoded** in [src/video_model.py](../src/video_model.py) (around lines 67-72):

```python
if sys.platform.startswith("win"):
    self.program_version = "7.6.0 (Windows)"
elif sys.platform.startswith("linux"):
    self.program_version = "7.6.0 (Linux)"
else:
    self.program_version = "7.6.0 (Unknown OS)"
```

**This string is stamped into every export's metadata JSON sidecar** (`Labeled_data/<video>/export/<video>_metadata.json`, `Program Version` field). Researchers reading old datasets rely on it to know which TinyTouch produced them, so it must match the git tag you're about to push.

Edit the three lines, commit, then push to master **before** tagging:

```bash
# After editing src/video_model.py
git add src/video_model.py
git commit -m "Bump version to 7.7.0"
git push origin master
```

Only then create the tag.

## Tag and push

The workflow triggers on tags matching `v*`:

```bash
git tag v7.7.0
git push origin v7.7.0
```

Rules for tag names:

- **Must start with `v`** (e.g. `v7.7.0`). Tags without `v` won't trigger any build.
- **Tags ending in `-legacy` are skipped** by every job — the legacy build runs on regular tags, this suffix is reserved.
- Use `MAJOR.MINOR.PATCH` semver. The latest tag at time of writing was `v7.6.0`.

If you tagged the wrong commit, see [Re-tagging](#re-tagging).

## What the workflow does

When you push a `v*` tag, [.github/workflows/build.yml](../.github/workflows/build.yml) runs three build jobs **in parallel** plus one release job that fans them in:

| Job | Runner | Python | Output |
| --- | --- | --- | --- |
| `build-windows` | `windows-latest` | 3.12 | `TinyTouch-v7.7.0-windows-x64.zip` (a single `TinyTouch-v7.7.0.exe` zipped) |
| `build-linux` | `ubuntu-latest` | 3.12 | `TinyTouch-v7.7.0-linux-x64.zip` |
| `build-linux-legacy` | `python:3.11-bullseye` container | 3.11 | `TinyTouch-v7.7.0-linux-legacy-x64.zip` (for older glibc systems) |
| `release` | `ubuntu-latest` | n/a | Aggregates the three zips into a GitHub Release named `TinyTouch v7.7.0` |

Each build runs `pip install -r requirements.txt`, then `python -m PyInstaller TinyTouch.spec` with `TINYTOUCH_APP_NAME=TinyTouch-<tag>`, then zips the dist output. The release job creates the GitHub Release using [`softprops/action-gh-release@v2`](https://github.com/softprops/action-gh-release).

A typical run takes **5-10 minutes**. The Windows job is usually the slowest.

## Monitor the build

1. Open <https://github.com/ctu-vras/touch-coder/actions> right after pushing the tag.
2. Click the run titled "Build TinyTouch" matching your tag.
3. Watch all three build jobs go green. If one fails, the `release` job is skipped — no Release is created. Fix the issue, then either re-tag (see [Re-tagging](#re-tagging)) or trigger a manual rebuild via the Actions UI.
4. Once `release` finishes, go to <https://github.com/ctu-vras/touch-coder/releases> — the new release should be at the top with the three zips attached.

## Verify the release

Quick post-release sanity check:

1. Download `TinyTouch-v7.7.0-windows-x64.zip` from the release page.
2. Extract and run `TinyTouch-v7.7.0.exe`.
3. Load any video, label one frame, save.
4. Open `Labeled_data/<video>/export/<video>_metadata.json` and confirm `"Program Version": "7.7.0 (Windows)"` matches the tag.
5. (Optional) Repeat on Linux if you have access — the legacy build is the one most likely to break first since it runs on Bullseye glibc.

## Manual builds without releasing

The workflow also has `workflow_dispatch`, so you can trigger a build from the Actions tab without creating a tag — useful for testing packaging changes on master.

1. <https://github.com/ctu-vras/touch-coder/actions/workflows/build.yml>
2. Click "Run workflow", pick a branch, click the green button.
3. Artifacts appear under the workflow run — there is **no GitHub Release** for `workflow_dispatch` (the `release` job's `if:` condition gates on `refs/tags/`).

This is the right way to validate a packaging change before committing to a tag.

## Rolling back a bad release

If you discover a problem **after** the release is published:

1. Go to <https://github.com/ctu-vras/touch-coder/releases>, click the bad release, click "Delete".
2. Delete the tag locally and remotely:
   ```bash
   git tag -d v7.7.0
   git push origin :refs/tags/v7.7.0
   ```
3. Fix the issue on `master` (commit + push as usual).
4. Re-tag with a **new** version number — never reuse a previously-published tag (users may have downloaded the bad zip and the tag is meant to be immutable).

If the build failed before the Release was created, just delete the tag and re-tag the right commit.

## Re-tagging

If you tagged the wrong commit but the workflow already ran (or partially ran):

```bash
# Delete the tag locally
git tag -d v7.7.0

# Delete it on the remote — this also stops any further runs from referring to it
git push origin :refs/tags/v7.7.0

# Re-tag the correct commit
git tag v7.7.0 <correct-sha>
git push origin v7.7.0
```

Don't do this if a Release was already published from the bad tag — go through [Rolling back a bad release](#rolling-back-a-bad-release) instead and bump the version.

## Versioning

TinyTouch follows [semver](https://semver.org/) loosely:

- **MAJOR** (`7.x.x` → `8.0.0`) — breaking change to the on-disk dataset format (CSV schema, metadata JSON keys, frames directory layout). Researchers' downstream pipelines break unless updated.
- **MINOR** (`7.6.x` → `7.7.0`) — new user-facing feature, new mode, new config key. Existing datasets keep working.
- **PATCH** (`7.6.0` → `7.6.1`) — bug fixes, performance, internal refactors. Behavior is the same from the user's point of view.

Recent history (most recent first): `v7.6.0`, `v7.5.7`, `v7.5.6`, `v7.5.5`, ...

## Common pitfalls

- **Forgot to bump `program_version` in `video_model.py`.** The release ships, but every metadata JSON written by it claims to be the *previous* version. Researchers will be confused. Fix by cutting a patch release with the right version string.
- **Tag without `v` prefix.** `7.7.0` (no `v`) does nothing — no jobs trigger. Just delete the tag and re-tag with `v7.7.0`.
- **Tag with `-legacy` suffix.** Every job is gated by `if: ${{ !endsWith(github.ref_name, '-legacy') }}`, so the workflow runs but immediately skips. Don't use `-legacy` in tag names; the *legacy build* runs on regular tags by virtue of the separate `build-linux-legacy` job.
- **Pushed tag before bumping version.** Use [Re-tagging](#re-tagging) to move the tag to the version-bump commit, or just push another tag (`v7.7.1`) with the right code.
- **New dependency added but `requirements.txt` not pinned.** PyInstaller may pick up a different version on the runners than you tested locally. Always pin (`package==X.Y.Z`) and verify with a manual `workflow_dispatch` build before tagging.
- **New asset under `icons/` not picked up by the bundle.** [TinyTouch.spec](../TinyTouch.spec) bundles the whole `icons/` tree as a `datas` entry, so this *should* work automatically — but if you add a folder elsewhere (e.g. `assets/audio/`), update `datas` in the spec.
