"""CESP v1.0 pack discovery, manifest loading and sound selection.

Pure stdlib on purpose: this module runs inside the hook process, which must
stay fast and must never depend on the managed venv.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import config

MANIFEST_NAME = "openpeon.json"
AUDIO_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac"}

# Spec 4.4 / appendix C. Lives here rather than in `registry` because both the
# installer and the player need it: spec 4.4 says players must reject a file
# that does not match its extension, and a pack copied into `~/.openpeon/packs`
# by hand never went through the installer.
AUDIO_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".wav": (b"RIFF",),
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"),
    ".ogg": (b"OggS",),
    ".flac": (b"fLaC",),
}


class PackError(Exception):
    """Raised when a pack directory is not a usable CESP pack."""


def looks_like_audio(path: Path) -> bool:
    """True when `path` starts with the magic bytes its extension promises.

    An extension we do not know is accepted: only the formats spec 4.4 names
    carry a mandated header, and a pack may legitimately ship something else.
    Unreadable files are rejected — there is nothing to play either way.
    """
    magic = AUDIO_MAGIC.get(Path(path).suffix.lower())
    if magic is None:
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return any(head.startswith(m) for m in magic)


def discover_packs(cwd: Path | None = None) -> dict[str, Path]:
    """Installed packs as {name: directory}, project-local shadowing global."""
    found: dict[str, Path] = {}
    for root in reversed(config.packs_dirs(cwd)):  # later dirs win, so walk backwards
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if (entry / MANIFEST_NAME).is_file():
                found[entry.name] = entry
    return found


def resolve_sound_path(pack_dir: Path, file_value: str) -> Path:
    """Resolve a manifest `file` entry to an absolute path inside the pack.

    Per spec 3.1 the path is relative to the manifest, uses forward slashes and
    must not escape the pack root. Bare filenames get the `sounds/` prefix that
    INTEGRATE.md prescribes.
    """
    raw = (file_value or "").strip()
    if not raw:
        raise PackError("empty sound path")
    if "\\" in raw:
        raise PackError(f"sound path must use forward slashes: {raw!r}")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise PackError(f"sound path must be relative: {raw!r}")
    if "/" not in raw:
        raw = f"sounds/{raw}"
    root = pack_dir.resolve()
    resolved = (root / raw).resolve()
    if resolved != root and root not in resolved.parents:
        raise PackError(f"sound path escapes the pack root: {file_value!r}")
    return resolved


def load_manifest(pack_dir: Path) -> dict:
    """Parse and sanity-check `openpeon.json`.

    Only the fields the player actually uses are validated; unknown extras are
    left untouched so newer minor spec revisions still load.
    """
    path = Path(pack_dir) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        raise PackError(f"no {MANIFEST_NAME} in {pack_dir}") from None
    except (OSError, ValueError) as e:
        raise PackError(f"cannot read {path}: {e}") from None

    if not isinstance(data, dict):
        raise PackError(f"{path}: manifest must be a JSON object")

    version = data.get("cesp_version")
    # Players must support every pack sharing their major version (spec 9.1).
    if not isinstance(version, str) or version.split(".")[0] != config.CESP_VERSION.split(".")[0]:
        raise PackError(f"{path}: unsupported cesp_version {version!r}")
    if not isinstance(data.get("name"), str) or not data["name"]:
        raise PackError(f"{path}: missing `name`")
    if not isinstance(data.get("categories"), dict):
        raise PackError(f"{path}: missing `categories`")

    data.setdefault("display_name", data["name"])
    aliases = data.get("category_aliases")
    data["category_aliases"] = aliases if isinstance(aliases, dict) else {}
    return data


def resolve_category(manifest: dict, category: str) -> list[dict]:
    """Sounds for a CESP category, following the alias chain (spec section 6).

    Lookup order: the name itself, then `category_aliases[name]` as spec 6
    prescribes, then — as an extension — the legacy key that aliases *onto*
    the requested CESP name, so a pack that never migrated its keys still
    plays. Returns an empty list when the pack has nothing for the category:
    that is a silent skip, never an error.
    """
    categories = manifest.get("categories") or {}
    aliases = manifest.get("category_aliases") or {}
    entry = categories.get(category)
    if entry is None:
        # Spec 6, step 2: the name we asked for may itself be a legacy one the
        # pack aliases onto a CESP category it does store.
        target = aliases.get(category)
        if isinstance(target, str):
            entry = categories.get(target)
    if entry is None:
        # Beyond spec 6: the reverse direction, for a pack that kept its sounds
        # under the legacy names and declared the aliases for older players.
        # A strict reading finds nothing there; we would rather play the sound.
        for legacy, cesp in aliases.items():
            if cesp == category and legacy in categories:
                entry = categories[legacy]
                break
    if not isinstance(entry, dict):
        return []
    sounds = entry.get("sounds")
    if not isinstance(sounds, list):
        return []
    return [s for s in sounds if isinstance(s, dict) and isinstance(s.get("file"), str)]


def select_sound(
    manifest: dict,
    pack_dir: Path,
    category: str,
    last_file: str | None = None,
    rng: random.Random | None = None,
) -> tuple[Path, dict] | None:
    """Pick a playable sound for `category`, avoiding an immediate repeat.

    Spec 8.1: random selection is recommended and the same sound should not
    play twice in a row. Entries whose file is missing or unreadable are
    dropped rather than played.
    """
    entries = resolve_category(manifest, category)
    candidates: list[tuple[Path, dict]] = []
    for entry in entries:
        try:
            path = resolve_sound_path(pack_dir, entry["file"])
        except PackError:
            continue
        if path.is_file():
            candidates.append((path, entry))
    if not candidates:
        return None

    pool = candidates
    if last_file and len(candidates) > 1:
        without_last = [c for c in candidates if str(c[0]) != last_file]
        if without_last:
            pool = without_last
    return (rng or random).choice(pool)


def active_pack(cfg: dict, cwd: Path | None = None) -> tuple[str, Path] | None:
    """The pack to play from: the configured one, or the sole installed one."""
    packs = discover_packs(cwd)
    if not packs:
        return None
    name = cfg.get("pack")
    if isinstance(name, str) and name:
        return (name, packs[name]) if name in packs else None
    if len(packs) == 1:
        return next(iter(packs.items()))
    # Several packs and no choice recorded — pick deterministically so the
    # user hears something consistent until they run /peon-set.
    name = sorted(packs)[0]
    return name, packs[name]
