"""Manifest loading, path safety, alias resolution and pack discovery."""
from __future__ import annotations

import json

import config
import pack as pack_mod
import pytest

from .conftest import make_pack


def test_discover_finds_packs_in_the_global_dir(paths):
    make_pack(paths / "packs", "peon", {"session.start": [("a.wav", "Ready to work?")]})
    found = pack_mod.discover_packs(paths / "project")
    assert set(found) == {"peon"}


def test_a_project_pack_shadows_the_global_one_of_the_same_name(paths):
    make_pack(paths / "packs", "peon", {"session.start": [("a.wav", "global")]})
    local_root = paths / "project" / ".openpeon" / "packs"
    make_pack(local_root, "peon", {"session.start": [("a.wav", "local")]})
    found = pack_mod.discover_packs(paths / "project")
    assert found["peon"] == local_root / "peon"


def test_directories_without_a_manifest_are_not_packs(paths):
    (paths / "packs" / "junk").mkdir()
    assert pack_mod.discover_packs(paths / "project") == {}


def test_load_manifest_defaults_optional_fields(paths):
    d = make_pack(paths / "packs", "peon", {"task.complete": [("a.wav", "Job's done!")]})
    manifest = pack_mod.load_manifest(d)
    assert manifest["display_name"] == "Peon"
    assert manifest["category_aliases"] == {}


@pytest.mark.parametrize("mutate,message", [
    (lambda m: m.pop("cesp_version"), "cesp_version"),
    (lambda m: m.update(cesp_version="2.0"), "cesp_version"),
    (lambda m: m.pop("name"), "name"),
    (lambda m: m.pop("categories"), "categories"),
])
def test_load_manifest_rejects_broken_manifests(paths, mutate, message):
    d = make_pack(paths / "packs", "peon", {"task.complete": [("a.wav", "x")]})
    manifest = json.loads((d / "openpeon.json").read_text())
    mutate(manifest)
    (d / "openpeon.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(pack_mod.PackError, match=message):
        pack_mod.load_manifest(d)


def test_load_manifest_accepts_a_future_minor_spec_revision(paths):
    d = make_pack(paths / "packs", "peon", {"task.complete": [("a.wav", "x")]},
                  cesp_version="1.7")
    assert pack_mod.load_manifest(d)["name"] == "peon"


def test_missing_manifest(paths):
    (paths / "packs" / "empty").mkdir()
    with pytest.raises(pack_mod.PackError):
        pack_mod.load_manifest(paths / "packs" / "empty")


# --- path safety (spec 3.1) ---

def test_bare_filenames_get_the_sounds_prefix(paths):
    d = paths / "packs" / "peon"
    assert pack_mod.resolve_sound_path(d, "Hello.wav") == (d / "sounds" / "Hello.wav").resolve()


def test_preview_paths_are_resolved_as_written(paths):
    d = paths / "packs" / "peon"
    assert pack_mod.resolve_sound_path(
        d, "preview.wav", prefix_sounds=False) == (d / "preview.wav").resolve()
    assert pack_mod.resolve_sound_path(
        d, "sounds/preview.wav", prefix_sounds=False) == (d / "sounds" / "preview.wav").resolve()


@pytest.mark.parametrize("bad", [
    "../../etc/passwd",
    "sounds/../../escape.wav",
    "/etc/passwd",
    "sounds\\Hello.wav",
    "",
    "   ",
])
def test_unsafe_sound_paths_are_rejected(paths, bad):
    with pytest.raises(pack_mod.PackError):
        pack_mod.resolve_sound_path(paths / "packs" / "peon", bad)


def test_a_nested_subdirectory_is_still_inside_the_pack(paths):
    d = paths / "packs" / "peon"
    assert pack_mod.resolve_sound_path(d, "sounds/en/Hello.wav").is_relative_to(d.resolve())


# --- category + alias resolution (spec section 6) ---

def test_resolve_category_direct_hit(paths):
    d = make_pack(paths / "packs", "peon", {"task.error": [("boom.wav", "Ugh.")]})
    sounds = pack_mod.resolve_category(pack_mod.load_manifest(d), "task.error")
    assert [s["label"] for s in sounds] == ["Ugh."]


def test_resolve_category_falls_back_through_an_alias(paths):
    # A legacy pack stores its sounds under `complete` and declares the alias.
    d = make_pack(paths / "packs", "old", {"complete": [("done.wav", "Job's done!")]},
                  aliases={"complete": "task.complete"})
    manifest = json.loads((d / "openpeon.json").read_text())
    manifest["cesp_version"] = "1.0"
    (d / "openpeon.json").write_text(json.dumps(manifest), encoding="utf-8")
    sounds = pack_mod.resolve_category(pack_mod.load_manifest(d), "task.complete")
    assert [s["label"] for s in sounds] == ["Job's done!"]


def test_resolve_category_follows_a_requested_legacy_name(paths):
    # Spec 6 order: the caller asks for `greeting`, the alias points at the
    # CESP key the pack actually stores.
    d = make_pack(paths / "packs", "new", {"session.start": [("hi.wav", "Ready to work.")]},
                  aliases={"greeting": "session.start"})
    sounds = pack_mod.resolve_category(pack_mod.load_manifest(d), "greeting")
    assert [s["label"] for s in sounds] == ["Ready to work."]


def test_the_direct_hit_wins_over_an_alias(paths):
    d = make_pack(paths / "packs", "both",
                  {"task.complete": [("new.wav", "new")], "complete": [("old.wav", "old")]},
                  aliases={"complete": "task.complete"})
    sounds = pack_mod.resolve_category(pack_mod.load_manifest(d), "task.complete")
    assert [s["label"] for s in sounds] == ["new"]


def test_an_alias_pointing_nowhere_is_not_an_error(paths):
    d = make_pack(paths / "packs", "peon", {"task.complete": [("a.wav", "x")]},
                  aliases={"greeting": "session.start"})
    assert pack_mod.resolve_category(pack_mod.load_manifest(d), "greeting") == []


# --- magic bytes (spec 4.4) ---

def test_a_wav_header_is_accepted(paths):
    d = make_pack(paths / "packs", "peon", {"task.complete": [("a.wav", "x")]})
    assert pack_mod.looks_like_audio(d / "sounds" / "a.wav")


def test_a_file_that_lies_about_its_extension_is_rejected(paths):
    d = make_pack(paths / "packs", "peon", {"task.complete": [("a.wav", "x")]})
    (d / "sounds" / "a.wav").write_bytes(b"#!/bin/sh\nrm -rf /\n")
    assert not pack_mod.looks_like_audio(d / "sounds" / "a.wav")


@pytest.mark.parametrize("name,head", [
    ("a.mp3", b"ID3\x04"),
    ("a.mp3", b"\xff\xfb\x90"),
    ("a.ogg", b"OggS\x00"),
    ("a.flac", b"fLaC\x00"),
])
def test_every_known_format_has_its_header(paths, name, head):
    path = paths / name
    path.write_bytes(head + b"\0" * 16)
    assert pack_mod.looks_like_audio(path)


def test_an_unknown_extension_is_left_alone(paths):
    path = paths / "a.aiff"
    path.write_bytes(b"whatever")
    assert pack_mod.looks_like_audio(path)


def test_an_unreadable_file_is_rejected(paths):
    assert not pack_mod.looks_like_audio(paths / "nope.wav")


def test_a_missing_category_is_an_empty_list_not_an_error(paths):
    d = make_pack(paths / "packs", "peon", {"task.complete": [("a.wav", "x")]})
    assert pack_mod.resolve_category(pack_mod.load_manifest(d), "resource.limit") == []


def test_malformed_sound_entries_are_dropped(paths):
    d = make_pack(paths / "packs", "peon", {"task.complete": [("a.wav", "x")]})
    manifest = json.loads((d / "openpeon.json").read_text())
    manifest["categories"]["task.complete"]["sounds"] += ["not-an-object", {"label": "no file"}]
    (d / "openpeon.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert len(pack_mod.resolve_category(pack_mod.load_manifest(d), "task.complete")) == 1


# --- active pack selection ---

def test_active_pack_uses_the_configured_name(paths):
    make_pack(paths / "packs", "peon", {"session.start": [("a.wav", "x")]})
    make_pack(paths / "packs", "glados", {"session.start": [("a.wav", "x")]})
    assert pack_mod.active_pack({"pack": "glados"})[0] == "glados"


def test_active_pack_defaults_to_the_only_installed_pack(paths):
    make_pack(paths / "packs", "peon", {"session.start": [("a.wav", "x")]})
    assert pack_mod.active_pack({"pack": None})[0] == "peon"


def test_active_pack_is_none_when_nothing_is_installed(paths):
    assert pack_mod.active_pack({"pack": None}) is None


def test_active_pack_is_none_when_the_configured_pack_is_gone(paths):
    make_pack(paths / "packs", "peon", {"session.start": [("a.wav", "x")]})
    assert pack_mod.active_pack({"pack": "deleted"}) is None


def test_round_robin_walks_the_configured_list(paths):
    make_pack(paths / "packs", "alpha", {"session.start": [("a.wav", "x")]})
    make_pack(paths / "packs", "bravo", {"session.start": [("b.wav", "x")]})
    packs = pack_mod.discover_packs()
    cfg = {"pack_rotation": ["alpha", "bravo"], "pack_rotation_mode": "round-robin"}
    assert pack_mod.next_rotated_pack(cfg, packs, None) == "alpha"
    assert pack_mod.next_rotated_pack(cfg, packs, "alpha") == "bravo"
    assert pack_mod.next_rotated_pack(cfg, packs, "bravo") == "alpha"


def test_rotation_skips_names_that_are_not_installed(paths):
    make_pack(paths / "packs", "alpha", {"session.start": [("a.wav", "x")]})
    packs = pack_mod.discover_packs()
    cfg = {"pack_rotation": ["ghost", "alpha", "deleted"], "pack_rotation_mode": "round-robin"}
    assert pack_mod.rotation_candidates(cfg, packs) == ["alpha"]
    assert pack_mod.next_rotated_pack(cfg, packs, None) == "alpha"


def test_rotation_off_or_empty_picks_nothing(paths):
    make_pack(paths / "packs", "alpha", {"session.start": [("a.wav", "x")]})
    packs = pack_mod.discover_packs()
    assert pack_mod.next_rotated_pack(
        {"pack_rotation": ["alpha"], "pack_rotation_mode": "off"}, packs) is None
    assert pack_mod.next_rotated_pack(
        {"pack_rotation": [], "pack_rotation_mode": "round-robin"}, packs) is None


def test_random_rotation_uses_the_injected_rng(paths):
    make_pack(paths / "packs", "alpha", {"session.start": [("a.wav", "x")]})
    make_pack(paths / "packs", "bravo", {"session.start": [("b.wav", "x")]})
    packs = pack_mod.discover_packs()
    cfg = {"pack_rotation": ["alpha", "bravo"], "pack_rotation_mode": "random"}

    class PickLast:
        def choice(self, seq):
            return seq[-1]

    assert pack_mod.next_rotated_pack(cfg, packs, rng=PickLast()) == "bravo"


def test_active_pack_prefers_the_session_pick_when_rotating(paths):
    make_pack(paths / "packs", "alpha", {"session.start": [("a.wav", "x")]})
    make_pack(paths / "packs", "bravo", {"session.start": [("b.wav", "x")]})
    cfg = {
        "pack": "alpha",
        "pack_rotation": ["alpha", "bravo"],
        "pack_rotation_mode": "round-robin",
    }
    assert pack_mod.active_pack(cfg, preferred="bravo")[0] == "bravo"


def test_active_pack_ignores_the_session_pick_when_rotation_is_off(paths):
    make_pack(paths / "packs", "alpha", {"session.start": [("a.wav", "x")]})
    make_pack(paths / "packs", "bravo", {"session.start": [("b.wav", "x")]})
    cfg = {"pack": "alpha", "pack_rotation": ["bravo"], "pack_rotation_mode": "off"}
    assert pack_mod.active_pack(cfg, preferred="bravo")[0] == "alpha"
