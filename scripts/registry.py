"""OpenPeon registry client: list, download and install CESP packs.

Stdlib only (`urllib` + `tarfile`), so `/peon-install` works before the
managed venv exists. Everything that lands on disk is validated against the
spec's limits (section 4) before it is accepted.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import config
import pack as pack_mod

USER_AGENT = f"peon-py/{config.PLUGIN_VERSION} (+https://openpeon.com)"

MAX_SOUND_BYTES = 1024 * 1024          # spec 4.2
MAX_ICON_BYTES = 500 * 1024            # spec 5.2
MAX_PREVIEW_BYTES = 500 * 1024         # spec 4.2
MAX_PACK_BYTES = 50 * 1024 * 1024      # spec 4.2
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024  # decompression bomb guard

AUDIO_MAGIC = pack_mod.AUDIO_MAGIC  # spec 4.4, shared with the playback path


class RegistryError(Exception):
    pass


def _get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            return resp.read(MAX_ARCHIVE_BYTES + 1)
    except Exception as e:
        raise RegistryError(f"fetching {url} failed: {e}") from None


def fetch_index(url: str = config.REGISTRY_INDEX_URL) -> list[dict]:
    """The registry's pack entries."""
    try:
        data = json.loads(_get(url).decode("utf-8"))
    except ValueError as e:
        raise RegistryError(f"registry index is not valid JSON: {e}") from None
    packs = data.get("packs") if isinstance(data, dict) else data
    if not isinstance(packs, list):
        raise RegistryError("registry index has no `packs` array")
    return [p for p in packs if isinstance(p, dict) and isinstance(p.get("name"), str)]


def find_entry(entries: list[dict], name: str) -> dict:
    for entry in entries:
        if entry.get("name") == name:
            return entry
    raise RegistryError(f"pack {name!r} is not in the registry")


def tarball_url(entry: dict) -> str:
    # The live index is flat (`source_repo`/`source_ref`); the registry-entry
    # schema nests the same fields under `source`. Accept either.
    source = entry.get("source")
    source = source if isinstance(source, dict) else {}
    repo = entry.get("source_repo") or source.get("repo")
    ref = entry.get("source_ref") or source.get("ref")
    if not repo or not ref:
        raise RegistryError(f"registry entry {entry.get('name')!r} has no source repo/ref")
    return f"https://github.com/{repo}/archive/refs/tags/{ref}.tar.gz"


def _member_target(member: tarfile.TarInfo, prefix: str) -> str | None:
    """Path of `member` relative to the pack root, or None to skip it.

    GitHub tarballs wrap everything in a `{repo}-{ref}/` directory; the pack
    itself lives at `source_path` inside that. Anything absolute, escaping via
    `..`, or not a regular file is dropped — we never trust the archive.
    """
    if not member.isfile():
        return None
    name = member.name.replace("\\", "/")
    if name.startswith("/") or ".." in name.split("/"):
        return None
    parts = name.split("/")
    if len(parts) < 2:
        return None
    rel = "/".join(parts[1:])  # strip the GitHub wrapper directory
    if prefix in ("", "."):
        return rel
    if rel == prefix or not rel.startswith(prefix + "/"):
        return None
    return rel[len(prefix) + 1:]


def _check_file(path: Path, rel: str) -> None:
    """Enforce the spec's per-file size and magic-byte rules."""
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if suffix in AUDIO_MAGIC:
        if size > MAX_SOUND_BYTES:
            raise RegistryError(f"{rel} is {size} bytes, over the 1 MB audio limit")
        with path.open("rb") as f:
            head = f.read(12)
        if not any(head.startswith(m) for m in AUDIO_MAGIC[suffix]):
            raise RegistryError(f"{rel} does not look like a valid {suffix} file")
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".svg") and size > MAX_ICON_BYTES:
        raise RegistryError(f"{rel} is {size} bytes, over the 500 KB icon limit")


def extract_pack(archive: Path, dest: Path, source_path: str = ".") -> None:
    """Extract the pack subtree of a GitHub tarball into `dest`."""
    prefix = (source_path or ".").strip("/")
    total = 0
    extracted = 0
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf:
            rel = _member_target(member, prefix)
            if rel is None:
                continue
            total += member.size
            if total > MAX_PACK_BYTES:
                raise RegistryError("pack exceeds the 50 MB total size limit")
            out = dest / rel
            src = tf.extractfile(member)
            if src is None:
                continue
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                with src, out.open("wb") as f:
                    shutil.copyfileobj(src, f)
            except OSError as e:
                # e.g. a name that is legal on Linux but reserved on Windows.
                raise RegistryError(f"cannot write {rel}: {e}") from None
            _check_file(out, rel)
            extracted += 1
    if extracted == 0:
        raise RegistryError(f"archive contained nothing under {source_path!r}")


def _check_preview(manifest: dict, pack_dir: Path) -> None:
    """Spec 4.2: a preview caps at 500 KB, not the 1 MB a sound gets."""
    preview = manifest.get("preview")
    if not isinstance(preview, str) or not preview.strip():
        return
    path = pack_mod.resolve_sound_path(pack_dir, preview, prefix_sounds=False)
    if not path.is_file():
        raise RegistryError(f"manifest declares a missing preview: {preview}")
    size = path.stat().st_size
    if size > MAX_PREVIEW_BYTES:
        raise RegistryError(f"{preview} is {size} bytes, over the 500 KB preview limit")


def verify_pack(pack_dir: Path) -> tuple[int, int]:
    """Validate the extracted pack. Returns (sound count, checksums verified).

    Sound files declaring a `sha256` are checked here, once, so that playback
    never has to pay for hashing.
    """
    manifest = pack_mod.load_manifest(pack_dir)
    _check_preview(manifest, pack_dir)
    sounds = 0
    verified = 0
    for category in manifest.get("categories", {}):
        for entry in pack_mod.resolve_category(manifest, category):
            path = pack_mod.resolve_sound_path(pack_dir, entry["file"])
            if not path.is_file():
                raise RegistryError(f"manifest references a missing file: {entry['file']}")
            sounds += 1
            digest = entry.get("sha256")
            if isinstance(digest, str) and digest:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != digest.lower():
                    raise RegistryError(f"checksum mismatch for {entry['file']}")
                verified += 1
    return sounds, verified


def source_path_of(entry: dict) -> str:
    source = entry.get("source")
    source = source if isinstance(source, dict) else {}
    return entry.get("source_path") or source.get("path") or "."


def verify_manifest_digest(pack_dir: Path, entry: dict) -> None:
    """Check the manifest against the digest the registry pinned for it.

    Every other checksum in play comes out of the manifest itself, which ships
    inside the same tarball — self-attested, so worthless against a compromised
    tag. `manifest_sha256` is the one value that arrives out of band, so a
    registry install refuses to proceed without it (the field is required by
    the registry-entry schema).
    """
    expected = entry.get("manifest_sha256")
    if not isinstance(expected, str) or not expected:
        raise RegistryError(
            f"registry entry {entry.get('name')!r} has no manifest_sha256; refusing to install")
    path = Path(pack_dir) / pack_mod.MANIFEST_NAME
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as e:
        raise RegistryError(f"cannot read the downloaded manifest: {e}") from None
    if actual != expected.strip().lower():
        raise RegistryError(
            f"manifest checksum mismatch for {entry.get('name')!r}: "
            f"registry pinned {expected.strip().lower()[:12]}…, got {actual[:12]}…")


def install_pack(name: str, packs_dir: Path | None = None,
                 index_url: str = config.REGISTRY_INDEX_URL) -> Path:
    """Download, verify and install a registry pack. Returns its directory.

    The pack is built in a temporary directory and only swapped into place once
    it validates, so a failed install can never leave a half-written pack.
    """
    target_root = Path(packs_dir) if packs_dir else config.GLOBAL_PACKS_DIR
    entry = find_entry(fetch_index(index_url), name)
    blob = _get(tarball_url(entry), timeout=120.0)
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise RegistryError("archive is implausibly large; refusing to install")

    target_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target_root) as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / "pack.tar.gz"
        archive.write_bytes(blob)
        staged = tmp_dir / "pack"
        extract_pack(archive, staged, source_path_of(entry))
        verify_manifest_digest(staged, entry)
        verify_pack(staged)

        final = target_root / name
        if final.exists():
            shutil.rmtree(final)
        staged.replace(final)
    return final
