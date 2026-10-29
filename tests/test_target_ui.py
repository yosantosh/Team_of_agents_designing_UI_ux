from __future__ import annotations

from ui_ux_company.registry import load_profile
from ui_ux_company.target_ui import guess_screen, load_manifest, scan_target_ui


def test_guess_screen():
    assert guess_screen("Profile_tab.png") == "profile"
    assert guess_screen("Home_feed.png") == "feed"
    assert guess_screen("notification.png") == "notifications"
    assert guess_screen("some_random_name.png") == "some_random_name"


def test_scan_target_ui_example(settings):
    profile = load_profile(settings)
    manifest = scan_target_ui(settings, profile)
    assert manifest.status in {"scanned", "not_available"}
    assert manifest.images, "expected at least one target image"
    image = manifest.images[0]
    assert image.sha256
    assert image.screen == "profile"
    assert image.width and image.height
    loaded = load_manifest(settings, profile)
    assert loaded.images[0].sha256 == image.sha256
