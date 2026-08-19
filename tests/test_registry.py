"""Registry client: archive extraction guards, validation, install flow."""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import tarfile

import config
import pack as pack_mod
import pytest
import registry

from .conftest import WAV_HEADER

ENTRY = {
    "name": "peon",
    "display_name": "Orc Peon",
    "source_repo": "PeonPing/og-packs",
    "source_ref": "v1.1.0",
    "source_path": "peon",
}


def build_tarball(members: dict[str, bytes], wrapper: str = "og-packs-1.1.0") -> bytes:
    """A GitHub-style tarball: everything under one wrapper directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(f"{wrapper}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def manifest_bytes(sounds: list[dict], name: str = "peon") -> bytes:
    return json.dumps({
        "cesp_version": "1.0",
        "name": name,
        "display_name": "Orc Peon",
        "version": "1.0.0",
        "categories": {"session.start": {"sounds": sounds}},
    }).encode()


def entry_for(members: dict[str, bytes], source_path: str = "peon", **over) -> dict:
    """A registry entry pinning the digest of `members`' manifest."""
    prefix = f"{source_path}/" if source_path not in ("", ".") else ""
    entry = dict(ENTRY, source_path=source_path,
                 manifest_sha256=hashlib.sha256(members[f"{prefix}openpeon.json"]).hexdigest())
    entry.update(over)
    return entry


def good_pack_members(source_path: str = "peon") -> dict[str, bytes]:
    prefix = f"{source_path}/" if source_path not in ("", ".") else ""
    audio = WAV_HEADER
    digest = hashlib.sha256(audio).hexdigest()
    return {
        f"{prefix}openpeon.json": manifest_bytes(
            [{"file": "sounds/Hello.wav", "label": "Ready to work?", "sha256": digest}]),
        f"{prefix}sounds/Hello.wav": audio,
        f"{prefix}README.md": b"# peon\n",
    }


# --- index parsing ---

def test_fetch_index_reads_the_packs_array(monkeypatch):
    monkeypatch.setattr(registry, "_get",
                        lambda url, timeout=30.0: json.dumps({"packs": [ENTRY]}).encode())
    assert registry.fetch_index()[0]["name"] == "peon"


def test_fetch_index_rejects_a_shapeless_document(monkeypatch):
    monkeypatch.setattr(registry, "_get", lambda url, timeout=30.0: b'{"nope": 1}')
    with pytest.raises(registry.RegistryError, match="packs"):
        registry.fetch_index()


def test_find_entry_reports_an_unknown_pack():
    with pytest.raises(registry.RegistryError, match="not in the registry"):
        registry.find_entry([ENTRY], "glados")


def test_tarball_url_uses_the_source_ref():
    assert registry.tarball_url(ENTRY) == \
        "https://github.com/PeonPing/og-packs/archive/refs/tags/v1.1.0.tar.gz"


def test_tarball_url_needs_a_source_repo():
    with pytest.raises(registry.RegistryError):
        registry.tarball_url({"name": "x"})


# --- extraction ---

def test_extract_strips_the_wrapper_and_source_path(paths, tmp_path):
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(good_pack_members()))
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, "peon")
    assert (dest / "openpeon.json").is_file()
    assert (dest / "sounds" / "Hello.wav").read_bytes() == WAV_HEADER


def test_extract_supports_a_repo_root_pack(paths, tmp_path):
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(good_pack_members(".")))
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, ".")
    assert (dest / "openpeon.json").is_file()


def test_files_outside_the_source_path_are_left_behind(paths, tmp_path):
    members = good_pack_members()
    members["glados/openpeon.json"] = manifest_bytes([], "glados")
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(members))
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, "peon")
    assert not (dest / "glados").exists()


@pytest.mark.parametrize("evil", [
    "peon/../../../../etc/passwd",
    "peon/../outside.wav",
])
def test_traversing_members_never_escape_the_destination(paths, tmp_path, evil):
    members = dict(good_pack_members())
    members[evil] = b"pwned"
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(members))
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, "peon")
    assert not (tmp_path / "outside.wav").exists()
    assert list(dest.rglob("passwd")) == []


def test_an_absolute_member_is_dropped(paths, tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("/etc/passwd")
        info.size = 5
        tf.addfile(info, io.BytesIO(b"pwned"))
        for name, data in good_pack_members().items():
            i = tarfile.TarInfo(f"og-packs-1.1.0/{name}")
            i.size = len(data)
            tf.addfile(i, io.BytesIO(data))
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(buf.getvalue())
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, "peon")
    assert sorted(p.name for p in dest.rglob("*") if p.is_file()) == \
        ["Hello.wav", "README.md", "openpeon.json"]


def test_an_empty_result_is_an_error(paths, tmp_path):
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(good_pack_members()))
    with pytest.raises(registry.RegistryError, match="nothing under"):
        registry.extract_pack(archive, tmp_path / "out", "glados")


def test_a_fake_wav_is_rejected(paths, tmp_path):
    members = good_pack_members()
    members["peon/sounds/Hello.wav"] = b"#!/bin/sh\nrm -rf /\n"
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(members))
    with pytest.raises(registry.RegistryError, match="valid .wav"):
        registry.extract_pack(archive, tmp_path / "out", "peon")


def test_an_oversized_sound_is_rejected(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "MAX_SOUND_BYTES", 8)
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(good_pack_members()))
    with pytest.raises(registry.RegistryError, match="audio limit"):
        registry.extract_pack(archive, tmp_path / "out", "peon")


def test_an_oversized_pack_is_rejected(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "MAX_PACK_BYTES", 10)
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(good_pack_members()))
    with pytest.raises(registry.RegistryError, match="50 MB"):
        registry.extract_pack(archive, tmp_path / "out", "peon")


# --- verification + install ---

def test_verify_counts_sounds_and_checksums(paths, tmp_path):
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(good_pack_members()))
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, "peon")
    assert registry.verify_pack(dest) == (1, 1)


def test_verify_rejects_a_checksum_mismatch(paths, tmp_path):
    members = good_pack_members()
    members["peon/openpeon.json"] = manifest_bytes(
        [{"file": "sounds/Hello.wav", "label": "x", "sha256": "0" * 64}])
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(members))
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, "peon")
    with pytest.raises(registry.RegistryError, match="checksum mismatch"):
        registry.verify_pack(dest)


def test_verify_rejects_a_manifest_pointing_at_a_missing_file(paths, tmp_path):
    members = good_pack_members()
    members["peon/openpeon.json"] = manifest_bytes(
        [{"file": "sounds/Missing.wav", "label": "x"}])
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(members))
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, "peon")
    with pytest.raises(registry.RegistryError, match="missing file"):
        registry.verify_pack(dest)


def _stub_network(monkeypatch, tarball: bytes, entry: dict | None = None):
    entry = entry if entry is not None else ENTRY

    def fake_get(url, timeout=30.0):
        if url.endswith(".tar.gz"):
            return tarball
        return json.dumps({"packs": [entry]}).encode()
    monkeypatch.setattr(registry, "_get", fake_get)


def test_install_lands_in_the_packs_dir(paths, monkeypatch):
    members = good_pack_members()
    _stub_network(monkeypatch, build_tarball(members), entry_for(members))
    dest = registry.install_pack("peon")
    assert dest == config.GLOBAL_PACKS_DIR / "peon"
    assert pack_mod.load_manifest(dest)["name"] == "peon"
    assert pack_mod.discover_packs(paths / "project") == {"peon": dest}


def test_install_replaces_a_previous_version(paths, monkeypatch):
    members = good_pack_members()
    _stub_network(monkeypatch, build_tarball(members), entry_for(members))
    registry.install_pack("peon")
    stale = config.GLOBAL_PACKS_DIR / "peon" / "sounds" / "Stale.wav"
    stale.write_bytes(WAV_HEADER)
    registry.install_pack("peon")
    assert not stale.exists()


def test_a_failed_install_leaves_no_partial_pack(paths, monkeypatch):
    members = good_pack_members()
    members["peon/sounds/Hello.wav"] = b"junk-not-audio"
    _stub_network(monkeypatch, build_tarball(members), entry_for(members))
    with pytest.raises(registry.RegistryError):
        registry.install_pack("peon")
    assert not (config.GLOBAL_PACKS_DIR / "peon").exists()


# --- registry-pinned manifest digest (registry-entry schema) ---

def test_install_rejects_a_manifest_the_registry_did_not_pin(paths, monkeypatch):
    # The tarball's manifest was swapped after the registry hashed it: same
    # sounds, but an extra category pointing wherever the attacker likes.
    members = good_pack_members()
    entry = entry_for(members)
    members["peon/openpeon.json"] = manifest_bytes(
        [{"file": "sounds/Hello.wav", "label": "tampered"}])
    _stub_network(monkeypatch, build_tarball(members), entry)
    with pytest.raises(registry.RegistryError, match="manifest checksum mismatch"):
        registry.install_pack("peon")
    assert not (config.GLOBAL_PACKS_DIR / "peon").exists()


def test_install_refuses_an_entry_without_a_manifest_digest(paths, monkeypatch):
    members = good_pack_members()
    _stub_network(monkeypatch, build_tarball(members), dict(ENTRY))  # ENTRY has none
    with pytest.raises(registry.RegistryError, match="no manifest_sha256"):
        registry.install_pack("peon")
    assert not (config.GLOBAL_PACKS_DIR / "peon").exists()


def test_a_pinned_digest_is_compared_case_insensitively(paths, tmp_path):
    members = good_pack_members()
    archive = tmp_path / "p.tar.gz"
    archive.write_bytes(build_tarball(members))
    dest = tmp_path / "out"
    registry.extract_pack(archive, dest, "peon")
    entry = entry_for(members)
    entry["manifest_sha256"] = entry["manifest_sha256"].upper()
    registry.verify_manifest_digest(dest, entry)  # must not raise


# --- source shape compatibility (flat index vs registry-entry schema) ---

def test_tarball_url_accepts_the_nested_source_object():
    entry = {"name": "peon",
             "source": {"type": "github", "repo": "PeonPing/og-packs", "ref": "v1.1.0"}}
    assert registry.tarball_url(entry) == \
        "https://github.com/PeonPing/og-packs/archive/refs/tags/v1.1.0.tar.gz"


def test_source_path_defaults_to_the_repo_root():
    assert registry.source_path_of({"name": "peon"}) == "."
    assert registry.source_path_of(ENTRY) == "peon"
    assert registry.source_path_of({"source": {"path": "packs/peon"}}) == "packs/peon"


# --- preview limit (spec 4.2) ---

def _pack_with_preview(tmp_path, preview_bytes: bytes) -> pathlib.Path:
    pack_dir = tmp_path / "pack"
    (pack_dir / "sounds").mkdir(parents=True)
    (pack_dir / "sounds" / "Hello.wav").write_bytes(WAV_HEADER)
    (pack_dir / "sounds" / "preview.wav").write_bytes(preview_bytes)
    manifest = json.loads(manifest_bytes([{"file": "sounds/Hello.wav", "label": "x"}]))
    manifest["preview"] = "sounds/preview.wav"
    (pack_dir / "openpeon.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack_dir


def test_an_oversized_preview_is_rejected(paths, tmp_path):
    # Under the 1 MB a sound may use, over the 500 KB a preview may use.
    pack_dir = _pack_with_preview(tmp_path, WAV_HEADER + b"\0" * (600 * 1024))
    with pytest.raises(registry.RegistryError, match="preview limit"):
        registry.verify_pack(pack_dir)


def test_a_preview_within_the_limit_passes(paths, tmp_path):
    pack_dir = _pack_with_preview(tmp_path, WAV_HEADER + b"\0" * 1024)
    assert registry.verify_pack(pack_dir) == (1, 0)


def test_a_declared_but_missing_preview_is_an_error(paths, tmp_path):
    pack_dir = _pack_with_preview(tmp_path, WAV_HEADER)
    (pack_dir / "sounds" / "preview.wav").unlink()
    with pytest.raises(registry.RegistryError, match="missing preview"):
        registry.verify_pack(pack_dir)
