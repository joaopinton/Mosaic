"""Ritaglio intelligente a formato (D-34): rapporto esatto, volti interi, bordi, soggetto senza volti."""

from fractions import Fraction

import numpy as np
import pytest
from PIL import Image

from mosaic.organize import smart_crop
from mosaic.organize.smart_crop import Format, crop_box, crop_size

PRESETS = [(1, 1), (4, 5), (3, 2), (2, 3), (16, 9), (9, 16), (210, 297), (297, 210)]
SIZES = [(6000, 4000), (4000, 6000), (4032, 3024), (3024, 4032), (1000, 1000), (5472, 3648), (1920, 1080)]


@pytest.mark.parametrize("fmt", PRESETS)
@pytest.mark.parametrize("size", SIZES)
def test_ratio_is_exact_and_the_box_is_as_big_as_possible(fmt, size):
    width, height = size
    x0, y0, x1, y1 = crop_box(width, height, Format(*fmt))
    assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height
    p, q = Fraction(*fmt).as_integer_ratio()
    if min(width // p, height // q) * max(p / width, q / height) >= 0.99:  # il multiplo intero perde meno dell'1%
        assert Fraction(x1 - x0, y1 - y0) == Fraction(*fmt)
    else:  # altrimenti il rapporto è giusto a meno di un pixel
        assert abs((x1 - x0) / (y1 - y0) - p / q) < 1 / min(x1 - x0, y1 - y0)
    assert (x1 - x0) >= width * 0.99 or (y1 - y0) >= height * 0.99  # un lato resta (quasi) intero


def test_awkward_ratios_stay_within_a_pixel():
    w, h = crop_size(4000, 4000, Format(1001, 1000))
    assert w == 4000 and abs(w / h - 1.001) < 1 / h  # il multiplo esatto perderebbe un quarto della foto


def test_same_shape_keeps_the_whole_photo():
    assert crop_box(6000, 4000, Format(3, 2)) == (0, 0, 6000, 4000)


def test_face_near_the_edge_pulls_the_box_to_the_border():
    left = crop_box(6000, 4000, Format(1, 1), faces=[(0.05, 0.3, 0.08, 0.12)])
    right = crop_box(6000, 4000, Format(1, 1), faces=[(0.87, 0.3, 0.08, 0.12)])
    assert left[0] == 0
    assert right[2] == 6000


def test_face_is_centred_when_there_is_room():
    x0, _, x1, _ = crop_box(6000, 4000, Format(4, 5), faces=[(0.6, 0.3, 0.06, 0.09)])
    face_cx = 0.63 * 6000
    assert abs((x0 + x1) / 2 - face_cx) < 0.03 * 6000


def _inside(box, face, width, height):
    x, y, w, h = face
    return box[0] <= x * width and (x + w) * width <= box[2] and box[1] <= y * height and (y + h) * height <= box[3]


def _cut(box, face, width, height):
    x, y, w, h = face
    seen_x = max(0.0, min(box[2], (x + w) * width) - max(box[0], x * width)) / (w * width)
    return 0.0 < seen_x < 1.0


def test_people_who_do_not_fit_are_never_cut_in_half():
    faces = [(0.1, 0.3, 0.08, 0.12), (0.45, 0.3, 0.08, 0.12), (0.82, 0.3, 0.08, 0.12)]
    box = crop_box(6000, 4000, Format(9, 16), faces=faces)
    assert not any(_cut(box, f, 6000, 4000) for f in faces)
    assert sum(_inside(box, f, 6000, 4000) for f in faces) >= 1


def test_the_bigger_face_wins_when_only_one_fits():
    small, big = (0.1, 0.35, 0.05, 0.07), (0.75, 0.3, 0.1, 0.15)
    box = crop_box(6000, 4000, Format(9, 16), faces=[small, big])
    assert _inside(box, big, 6000, 4000) and not _cut(box, small, 6000, 4000)


def test_two_close_people_both_stay_inside():
    faces = [(0.38, 0.3, 0.07, 0.1), (0.5, 0.32, 0.07, 0.1)]
    box = crop_box(6000, 4000, Format(1, 1), faces=faces)
    assert all(_inside(box, f, 6000, 4000) for f in faces)


def test_two_faces_that_barely_fit_are_both_kept_whole():
    # dalla libreria vera: tre volti, i due a sinistra entrano in 9:16 solo senza margine per i capelli
    faces = [(0.205, 0.457, 0.29, 0.485), (0.644, 0.275, 0.176, 0.279), (0.446, 0.311, 0.171, 0.275)]
    box = crop_box(5152, 3864, Format(9, 16), faces=faces)
    assert _inside(box, faces[0], 5152, 3864) and _inside(box, faces[2], 5152, 3864)
    assert not _cut(box, faces[1], 5152, 3864)


def test_a_face_bigger_than_the_box_stays_centred():
    face = (0.28, 0.32, 0.44, 0.66)  # selfie: il volto è più largo del riquadro 9:16
    x0, _, x1, _ = crop_box(5152, 3864, Format(9, 16), faces=[face])
    assert abs((x0 + x1) / 2 / 5152 - 0.5) < 0.02


def test_portrait_to_landscape_keeps_the_head_with_headroom():
    face = (0.4, 0.15, 0.2, 0.12)
    width, height = 4000, 6000
    x0, y0, x1, y1 = crop_box(width, height, Format(16, 9), faces=[face])
    assert _inside((x0, y0, x1, y1), face, width, height)
    face_cy = (face[1] + face[3] / 2) * height
    assert (face_cy - y0) / (y1 - y0) < 0.5  # il volto nella metà alta, non al centro esatto


def test_without_faces_follows_the_subject():
    img = Image.new("RGB", (900, 600), (128, 128, 128))
    rng = np.random.default_rng(1)
    patch = (rng.random((180, 160, 3)) * 255).astype(np.uint8)
    img.paste(Image.fromarray(patch), (60, 220))  # soggetto pieno di dettagli a sinistra
    x0, _, x1, _ = crop_box(900, 600, Format(1, 1), sal=smart_crop.saliency(img))
    assert x0 <= 60 and x1 >= 220


def test_without_faces_a_coloured_subject_counts_too():
    from PIL import ImageDraw, ImageFilter

    img = Image.new("RGB", (600, 900), (70, 90, 110))
    ImageDraw.Draw(img).ellipse((200, 640, 400, 840), fill=(220, 40, 30))  # palla rossa in basso
    img = img.filter(ImageFilter.GaussianBlur(4))
    _, y0, _, y1 = crop_box(600, 900, Format(1, 1), sal=smart_crop.saliency(img))
    assert y0 <= 640 and y1 >= 840


def test_without_anything_stays_a_little_above_the_middle():
    flat = Image.new("RGB", (400, 600), (90, 90, 90))
    x0, y0, x1, y1 = crop_box(4000, 6000, Format(1, 1), sal=smart_crop.saliency(flat))
    assert (x0, x1) == (0, 4000)
    assert abs((y0 + y1) / 2 / 6000 - 0.45) < 0.03
    assert crop_box(6000, 4000, Format(1, 1))[0] == 1000  # niente mappa: al centro


def test_pixel_formats_come_out_at_the_exact_size():
    img = Image.new("RGB", (640, 480), (10, 20, 30))
    fmt = Format(1080, 1350, pixels=True)
    out = smart_crop.apply(img, crop_box(640, 480, fmt), fmt)
    assert out.size == (1080, 1350)
    ratio = Format(4, 5)
    assert smart_crop.apply(img, crop_box(640, 480, ratio), ratio).size == (384, 480)


@pytest.mark.parametrize("bad", [(0, 1), (1, 0), (-3, 2), (20001, 2), (11, 1), (1, 11)])
def test_bad_formats_are_refused(bad):
    with pytest.raises(ValueError):
        Format(*bad)
