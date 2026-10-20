"""Migliora luce e colori (D-30): soggetto più luminoso, alte luci salve, vibrance, tono coordinato."""

import cv2
import numpy as np
import pytest
from PIL import Image

from mosaic.organize import enhance


def scene(subject: int, background: int = 60, size=(300, 200), tint=(0, 0, 0)) -> Image.Image:
    """Fondo scuro con un «volto» al centro e una lampada bianca in un angolo."""
    w, h = size
    img = np.full((h, w, 3), background, np.uint8)
    img[60:140, 110:190] = subject
    img[0:20, 0:20] = 250
    img = np.clip(img.astype(np.int16) + np.array(tint, np.int16), 0, 255).astype(np.uint8)
    return Image.fromarray(img)


FACE = [(110 / 300, 60 / 200, 80 / 300, 80 / 200)]


def lum(img: Image.Image, box) -> float:
    x0, y0, x1, y1 = box
    lab = cv2.cvtColor(np.asarray(img)[y0:y1, x0:x1], cv2.COLOR_RGB2LAB)
    return float(lab[..., 0].mean()) / 255


def test_dark_subject_gets_light_without_burning_highlights():
    img = scene(subject=45)
    stats = enhance.measure(img, FACE)
    [adjust] = enhance.plan([stats], enhance.Options())
    out = enhance.apply(img, adjust)
    assert adjust.gamma < 1
    assert lum(out, (110, 60, 190, 140)) > lum(img, (110, 60, 190, 140)) + 0.08
    assert lum(out, (0, 0, 20, 20)) <= 1.0 and np.asarray(out)[5, 5].min() >= 245  # la lampada resta bianca, non sparisce
    assert out.size == img.size


def test_well_exposed_subject_is_left_almost_alone():
    img = scene(subject=150, background=120)
    [adjust] = enhance.plan([enhance.measure(img, FACE)], enhance.Options(contrast=False, vivid=False))
    assert adjust.gamma == pytest.approx(1.0, abs=0.05)


def test_vibrance_lifts_dull_colors_more_than_vivid_ones():
    dull = np.full((10, 20, 3), (140, 120, 110), np.uint8)
    vivid = np.full((10, 20, 3), (230, 30, 30), np.uint8)
    img = Image.fromarray(np.concatenate([dull, vivid]))
    out = enhance.apply(img, enhance.Adjust(vibrance=0.35))

    def chroma(a):
        lab = cv2.cvtColor(a, cv2.COLOR_RGB2LAB).astype(np.float32)
        return float(np.hypot(lab[..., 1] - 128, lab[..., 2] - 128).mean())

    before, after = np.asarray(img), np.asarray(out)
    gain_dull = chroma(after[:10]) / chroma(before[:10])
    gain_vivid = chroma(after[10:]) / chroma(before[10:])
    assert gain_dull > 1.1 and gain_dull > gain_vivid


def test_harmonize_brings_the_group_closer():
    group = [scene(subject=110, tint=(25, 0, -25)), scene(subject=110), scene(subject=110, tint=(-20, 0, 25))]
    opts = enhance.Options(light=False, contrast=False, vivid=False)
    stats = [enhance.measure(g, FACE) for g in group]
    adjusts = enhance.plan(stats, opts)
    after = [enhance.measure(enhance.apply(g, a), FACE) for g, a in zip(group, adjusts, strict=True)]
    assert np.std([s.b for s in after]) < np.std([s.b for s in stats]) * 0.7
    assert np.std([s.a for s in after]) <= np.std([s.a for s in stats])


def test_nothing_to_do_and_bad_options():
    assert enhance.plan([], enhance.Options()) == []
    off = enhance.Options(light=False, contrast=False, vivid=False, harmonize=False)
    assert enhance.plan([enhance.measure(scene(80))], off)[0].identity
    with pytest.raises(ValueError):
        enhance.Options(strength="tanta")


def test_large_images_are_processed_in_strips():
    img = scene(subject=50, size=(400, enhance.STRIP * 2 + 37))
    [adjust] = enhance.plan([enhance.measure(img)], enhance.Options())
    out = enhance.apply(img, adjust)
    rows = np.asarray(out)[:, 200].astype(int)
    assert abs(rows[enhance.STRIP - 1] - rows[enhance.STRIP]).max() < 3  # nessuna cucitura tra le strisce
