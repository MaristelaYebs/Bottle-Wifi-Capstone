"""Tests for the dependency-free parts of the live training collector."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_training import find_camera, full_frame_label, split_for_sample  # noqa: E402


def test_split_is_70_20_10():
    splits = [split_for_sample(index) for index in range(1, 11)]
    assert splits == ["train"] * 7 + ["val"] * 2 + ["test"]
    assert split_for_sample(11) == "train"
    print("ok: live collector uses deterministic 70/20/10 splits")


def test_full_frame_label():
    assert full_frame_label(0) == "0 0.5 0.5 1.0 1.0\n"
    assert full_frame_label(1) == "1 0.5 0.5 1.0 1.0\n"
    print("ok: bottle and reject labels use full-frame YOLO boxes")


def test_camera_selection_returns_a_usable_index():
    index, name = find_camera(default_index=7)
    assert isinstance(index, int)
    assert name
    print("ok: camera selection has a numeric fallback")


if __name__ == "__main__":
    test_split_is_70_20_10()
    test_full_frame_label()
    test_camera_selection_returns_a_usable_index()
    print("\nALL LIVE TRAINING TESTS PASSED")