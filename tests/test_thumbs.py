from pathlib import Path

import pytest
from PIL import Image

from mosaic.thumbs import colors
from mosaic.thumbs.render import ensure_thumb, generate_set, open_image, thumb_path
from mosaic.thumbs.sizes import SIZES, get

SHA = "ab1234567890abcdef1234567890abcdef123456"


def big_jpeg(path: Path, size=(4000, 3000), color=(30, 90, 200)) -> Path:
    Image.new("RGB", size, color).save(path, "JPEG", quality=85)
    return path


def test_layout_of_cache_path(ctx):
    p = thumb_path(ctx.thumbs_dir, SHA, get("fit_720"))
    assert p.parts[-4:-1] == ("a", "b", "1") and p.name == f"{SHA}_720x720_fit.jpg"
    assert thumb_path(ctx.thumbs_dir, SHA, get("tile_224")).name == f"{SHA}_224x224_tile.jpg"


def test_generate_default_set_with_correct_dimensions(ctx, tmp_path):
    src = big_jpeg(tmp_path / "big.jpg")
    paths = generate_set(ctx, SHA, src)
    assert set(paths) == set(ctx.cfg.thumbs.pregenerate)
    for name, path in paths.items():
        assert path.exists(), name
        w, h = Image.open(path).size
        size = SIZES[name]
        if size.crop:
            assert w == h == size.w
        else:
            assert w <= size.w and h <= size.h and max(w / size.w, h / size.h) > 0.99
    assert Image.open(paths["fit_1920"]).size == (1600, 1200)  # 4000x3000 dentro 1920x1200


def test_small_images_are_never_upscaled(ctx, tmp_path):
    src = big_jpeg(tmp_path / "small.jpg", size=(64, 48))
    paths = generate_set(ctx, SHA, src)
    assert Image.open(paths["fit_1920"]).size == (64, 48)
    assert Image.open(paths["tile_500"]).size == (48, 48)
    assert Image.open(paths["tile_50"]).size == (48, 48)


def test_second_run_does_not_rewrite_files(ctx, tmp_path):
    src = big_jpeg(tmp_path / "big.jpg", size=(800, 600))
    first = generate_set(ctx, SHA, src)
    stamps = {n: p.stat().st_mtime_ns for n, p in first.items()}
    second = generate_set(ctx, SHA, src)
    assert stamps == {n: p.stat().st_mtime_ns for n, p in second.items()}


def test_on_demand_large_size(ctx, tmp_path):
    src = big_jpeg(tmp_path / "big.jpg", size=(3000, 2000))
    path = ensure_thumb(ctx, SHA, src, "fit_2560")
    assert Image.open(path).size == (2400, 1600)
    with pytest.raises(KeyError):
        ensure_thumb(ctx, SHA, src, "fit_9999")


def test_exif_orientation_is_applied(tmp_path):
    path = tmp_path / "rot.jpg"
    exif = Image.Exif()
    exif[274] = 6  # ruotata di 90°
    Image.new("RGB", (200, 100), (255, 0, 0)).save(path, "JPEG", exif=exif)
    assert open_image(path).size == (100, 200)


def test_png_with_alpha_is_flattened_on_white(tmp_path):
    path = tmp_path / "a.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(path)
    assert open_image(path).getpixel((5, 5)) == (255, 255, 255)


def test_color_names():
    assert colors.color_name(10, 10, 10) == "Nero"
    assert colors.color_name(250, 250, 250) == "Bianco"
    assert colors.color_name(128, 128, 128) == "Grigio"
    assert colors.color_name(220, 30, 30) == "Rosso"
    assert colors.color_name(240, 130, 20) == "Arancione"
    assert colors.color_name(240, 220, 20) == "Giallo"
    assert colors.color_name(30, 180, 60) == "Verde"
    assert colors.color_name(30, 200, 230) == "Azzurro"
    assert colors.color_name(20, 40, 200) == "Blu"
    assert colors.color_name(140, 40, 200) == "Viola"
    assert colors.color_name(240, 100, 180) == "Rosa"
    assert colors.color_name(110, 60, 20) == "Marrone"


def test_dominant_colors_of_two_tone_image():
    img = Image.new("RGB", (64, 64), (200, 20, 20))
    img.paste((20, 20, 200), (0, 0, 20, 64))
    info = colors.analyze(img)
    assert info.main == "Rosso" and "Blu" in info.names
    assert 0 < info.chroma <= 1 and 0 < info.luminance < 1
    assert colors.analyze(Image.new("RGB", (8, 8), (255, 255, 255))).main == "Bianco"
