from __future__ import annotations

from ui_ux_company.registry import load_profile
from pathlib import Path

from ui_ux_company.target_ui import (
    guess_screen,
    guess_state,
    load_manifest,
    save_manifest,
    scan_target_ui,
)


def test_guess_screen():
    assert guess_screen("Profile_tab.png") == "profile"
    assert guess_screen("Home_feed.png") == "feed"
    assert guess_screen("notification.png") == "notifications"
    assert guess_screen("MusicTab_reaction_share.png") == "player"
    assert guess_screen("MessageTab_ChatBetweenUsers.png") == "messages"
    assert guess_screen("InitialscreenOrbitu.png") == "splash"
    assert guess_screen("some_random_name.png") == "some_random_name"
    assert guess_screen("MessageTab_ChatBetweenUsers_bottom_tab_rows_should_be_like_feed_image.png") == "messages"
    assert guess_screen("Feed_TopRightMusicIconShouldBeReplaceWithBellIconForNotfication.png") == "feed"
    assert guess_state("Feed_empty_offline.png") == "offline"
    assert guess_state("Profile_edit.png") == "edit"


def test_scan_target_ui_example(settings):
    profile = load_profile(settings)
    manifest = scan_target_ui(settings, profile)
    assert manifest.status in {"scanned", "not_available"}
    assert manifest.images, "expected at least one target image"
    image = manifest.images[0]
    assert image.sha256
    assert image.filename
    assert image.screen == "profile"
    assert image.screen_hint == "profile"
    assert image.analysis_is_advisory is True
    assert image.width and image.height
    loaded = load_manifest(settings, profile)
    assert loaded.images[0].sha256 == image.sha256


def test_rescan_preserves_annotations_with_full_filename(settings):
    profile = load_profile(settings)
    manifest = scan_target_ui(settings, profile)
    image = manifest.images[0]
    image.annotations = [
        {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "label": "header", "file": image.file, "filename": Path(image.file).name}
    ]
    save_manifest(settings, profile, manifest)
    rescanned = scan_target_ui(settings, profile)
    restored = rescanned.images[0]
    assert restored.filename == Path(restored.file).name
    assert restored.annotations[0]["filename"] == restored.filename
