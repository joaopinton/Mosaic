from pathlib import Path

from mosaic.index.discover import FileEntry, classify, extension_matches, sniff_family
from mosaic.index.related import group_directory, group_key, raw_prefix, strip_sequence


def entry(name: str, directory: str = "") -> FileEntry:
    return FileEntry(name, directory, Path(name), 1, 1.0)


def test_raw_prefix_strips_all_known_extensions():
    assert raw_prefix("IMG_1.jpg.xmp") == "IMG_1"
    assert raw_prefix("IMG_1.CR2") == "IMG_1"
    assert raw_prefix("holiday.2024.jpg") == "holiday.2024"


def test_sequence_stripping():
    assert strip_sequence("IMG_1 (2)") == "IMG_1"
    assert strip_sequence("IMG_1 copy 2") == "IMG_1"
    assert strip_sequence("IMG_1.00001") == "IMG_1"
    assert strip_sequence("IMG_1") == "IMG_1"


def test_group_key_apple_edit_and_case():
    assert group_key("IMG_E1234.JPG") == group_key("img_1234.heic")


def test_stack_of_four_files_has_jpeg_primary():
    names = ["IMG_1.jpg", "IMG_1.CR2", "IMG_1.jpg.xmp", "IMG_1.mov"]
    groups = group_directory("", [entry(n) for n in names])
    assert len(groups) == 1
    assert groups[0].main.name == "IMG_1.jpg"
    assert len(groups[0].files) == 4


def test_sequences_only_stack_when_enabled():
    names = [entry("a.jpg"), entry("a (2).jpg")]
    assert len(group_directory("", names, stack_sequences=False)) == 2
    assert len(group_directory("", names, stack_sequences=True)) == 1


def test_orphan_sidecar_is_discarded():
    assert group_directory("", [entry("lonely.xmp")]) == []


def test_raw_beats_video_and_heic_in_priority():
    groups = group_directory("", [entry("x.mov"), entry("x.heic"), entry("x.nef")])
    assert groups[0].main.name == "x.nef"


def test_classify_and_sniff(tmp_path: Path):
    assert classify("a.JPG") == "image" and classify("a.mov") == "video" and classify("a.txt") is None
    png = tmp_path / "fake.jpg"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    assert sniff_family(png.read_bytes()[:16]) == "png"
    assert extension_matches(png) is False
    ok = tmp_path / "ok.jpg"
    ok.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 20)
    assert extension_matches(ok) is True
