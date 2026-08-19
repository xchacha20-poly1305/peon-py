"""Sound selection: no-repeat, missing files, deterministic picking."""
from __future__ import annotations

import random

import pack as pack_mod

from .conftest import make_pack

THREE = {"task.complete": [("a.wav", "A"), ("b.wav", "B"), ("c.wav", "C")]}


def test_selects_a_playable_sound(paths):
    d = make_pack(paths / "packs", "peon", THREE)
    path, entry = pack_mod.select_sound(pack_mod.load_manifest(d), d, "task.complete")
    assert path.is_file()
    assert entry["label"] in {"A", "B", "C"}


def test_never_repeats_the_previous_sound(paths):
    d = make_pack(paths / "packs", "peon", THREE)
    manifest = pack_mod.load_manifest(d)
    last = str((d / "sounds" / "a.wav").resolve())
    rng = random.Random(0)
    for _ in range(50):
        path, _ = pack_mod.select_sound(manifest, d, "task.complete", last, rng)
        assert str(path) != last


def test_a_single_sound_category_repeats_rather_than_going_silent(paths):
    # Spec 8.1 only asks to avoid repeats when there is an alternative.
    d = make_pack(paths / "packs", "peon", {"task.error": [("only.wav", "Ugh.")]})
    only = str((d / "sounds" / "only.wav").resolve())
    path, _ = pack_mod.select_sound(pack_mod.load_manifest(d), d, "task.error", only)
    assert str(path) == only


def test_entries_whose_file_is_missing_are_skipped(paths):
    d = make_pack(paths / "packs", "peon", THREE)
    (d / "sounds" / "a.wav").unlink()
    (d / "sounds" / "b.wav").unlink()
    for _ in range(20):
        path, entry = pack_mod.select_sound(pack_mod.load_manifest(d), d, "task.complete")
        assert entry["label"] == "C"


def test_returns_none_when_every_file_is_missing(paths):
    d = make_pack(paths / "packs", "peon", THREE, create_files=False)
    assert pack_mod.select_sound(pack_mod.load_manifest(d), d, "task.complete") is None


def test_returns_none_for_a_category_the_pack_does_not_have(paths):
    d = make_pack(paths / "packs", "peon", THREE)
    assert pack_mod.select_sound(pack_mod.load_manifest(d), d, "user.spam") is None


def test_all_candidates_are_reachable(paths):
    d = make_pack(paths / "packs", "peon", THREE)
    manifest = pack_mod.load_manifest(d)
    rng = random.Random(1234)
    seen = {pack_mod.select_sound(manifest, d, "task.complete", None, rng)[1]["label"]
            for _ in range(100)}
    assert seen == {"A", "B", "C"}
