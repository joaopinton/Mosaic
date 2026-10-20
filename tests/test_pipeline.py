
from conftest import make_jpeg, make_video, needs_exiftool, needs_ffmpeg, write_tags
from PIL import Image

from mosaic.index.indexer import index_roots
from mosaic.meta import sources
from mosaic.pipeline import process_photo
from mosaic.thumbs.render import thumb_path
from mosaic.thumbs.sizes import get


def run(ctx) -> list[int]:
    stats = index_roots(ctx)
    for pid in stats.changed_photo_ids:
        assert process_photo(ctx, pid)
    return stats.changed_photo_ids


def photo(ctx, pid: int):
    return ctx.db.one("SELECT * FROM photos WHERE id=?", (pid,))


def test_plain_jpeg_gets_thumbs_colors_quality_and_search_row(ctx, library):
    make_jpeg(library / "IMG_20230501_123456.jpg", color=(210, 20, 20), size=(1400, 1000))
    (pid,) = run(ctx)
    p = photo(ctx, pid)
    assert (p["width"], p["height"]) == (1400, 1000)
    assert p["taken_at"] == "2023-05-01T12:34:56Z" and p["taken_src"] == sources.NAME
    assert p["main_color"] == "Rosso" and p["meta_at"]
    sha1 = ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (pid,))
    assert thumb_path(ctx.thumbs_dir, sha1, get("fit_720")).exists()
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos_fts WHERE photos_fts MATCH '\"IMG_20230501_123456\"'") == 1
    assert p["quality"] == 1  # solo la risoluzione ≥ 1 MP


def test_pillow_exif_fallback_reads_camera_and_date(ctx, library, monkeypatch):
    monkeypatch.setattr("mosaic.meta.exiftool.ExifTool.available", property(lambda self: False))
    exif = Image.Exif()
    exif[271], exif[272] = "NIKON CORPORATION", "NIKON D850"
    exif.get_ifd(0x8769)[36867] = "2022:08:15 18:00:00"
    path = library / "a.jpg"
    Image.new("RGB", (80, 60), (1, 2, 3)).save(path, "JPEG", exif=exif)
    (pid,) = run(ctx)
    p = photo(ctx, pid)
    cam = ctx.db.one("SELECT * FROM cameras WHERE id=?", (p["camera_id"],))
    assert (cam["make"], cam["model"]) == ("Nikon", "D850")
    assert p["taken_at"] == "2022-08-15T18:00:00Z" and p["taken_src"] == sources.META


@needs_exiftool
def test_exiftool_reads_gps_title_and_caches_json(ctx, library):
    path = make_jpeg(library / "gps.jpg", size=(640, 480))
    write_tags(path, GPSLatitude="45.4", GPSLatitudeRef="N", GPSLongitude="11.8", GPSLongitudeRef="E",
               DateTimeOriginal="2023:05:01 12:34:56", Make="NIKON CORPORATION", Model="NIKON D850", ISO="200",
               Title="Tramonto sul Brenta", Subject="mare")
    (pid,) = run(ctx)
    p = photo(ctx, pid)
    assert round(p["lat"], 2) == 45.4 and round(p["lng"], 2) == 11.8
    assert p["title"] == "Tramonto sul Brenta" and p["title_src"] == sources.META
    assert p["iso"] == 200 and p["quality"] == 3  # data affidabile + GPS + fotocamera; 640x480 < 1 MP
    sha1 = ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (pid,))
    assert (ctx.exif_dir / sha1[:2] / f"{sha1}.json").exists()
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos_keywords") == 1


@needs_exiftool
def test_xmp_sidecar_wins_over_embedded_and_blocks_llm(ctx, library):
    path = make_jpeg(library / "x.jpg", size=(320, 240))
    write_tags(path, Title="Titolo embedded")
    (library / "x.jpg.xmp").write_text(
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="3">'
        '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Titolo da XMP</rdf:li></rdf:Alt></dc:title>'
        '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>', encoding="utf-8")
    (pid,) = run(ctx)
    p = photo(ctx, pid)
    assert p["title"] == "Titolo da XMP" and p["title_src"] == sources.XMP and p["rating"] == 3
    assert not sources.set_text(ctx.db.conn(), pid, "title", "Idea dell'LLM", sources.LLM)


def test_reprocessing_is_idempotent(ctx, library):
    make_jpeg(library / "a.jpg", size=(500, 400))
    (pid,) = run(ctx)
    before = photo(ctx, pid)["created_at"]
    assert process_photo(ctx, pid)
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 1
    assert photo(ctx, pid)["created_at"] == before


def test_stack_by_xmp_uuid_merges_photos(ctx, library, monkeypatch):
    from mosaic.meta.extract import MetaData

    make_jpeg(library / "a.jpg", color=(1, 1, 1))
    make_jpeg(library / "b.jpg", color=(200, 1, 1))
    monkeypatch.setattr("mosaic.pipeline.extract.extract", lambda data, name, mtime: MetaData(uuid="xmp.did:SAME"))
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM files") == 2


@needs_ffmpeg
def test_video_gets_poster_duration_and_type(ctx, library):
    make_video(library / "clip.mp4", seconds=3)
    (pid,) = run(ctx)
    p = photo(ctx, pid)
    assert p["type"] == "video" and abs(p["duration"] - 3) < 0.5
    assert (p["width"], p["height"]) == (160, 120)
    sha1 = ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (pid,))
    assert (ctx.sidecar_dir / f"{sha1}.jpg").exists()
    assert thumb_path(ctx.thumbs_dir, sha1, get("tile_224")).exists()
    assert ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE type='transcode'") == 0  # h264/yuv420p è già web


@needs_ffmpeg
def test_short_video_next_to_image_makes_live_photo(ctx, library):
    make_jpeg(library / "IMG_1.jpg", size=(320, 240))
    make_video(library / "IMG_1.mov", seconds=2)
    (pid,) = run(ctx)
    assert photo(ctx, pid)["type"] == "live"
    assert ctx.db.scalar("SELECT COUNT(*) FROM files WHERE photo_id=?", (pid,)) == 2


@needs_ffmpeg
def test_long_video_next_to_image_is_not_live(ctx, library):
    make_jpeg(library / "IMG_2.jpg", size=(320, 240))
    make_video(library / "IMG_2.mov", seconds=5)
    (pid,) = run(ctx)
    assert photo(ctx, pid)["type"] == "image"


@needs_ffmpeg
def test_non_web_video_enqueues_transcode(ctx, library):
    make_video(library / "old.mp4", seconds=2, codec="mpeg4")
    (pid,) = run(ctx)
    assert ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE type='transcode'") == 1


@needs_ffmpeg
@needs_exiftool
def test_google_motion_photo_is_extracted(ctx, library):
    video = make_video(library / "tmp_clip.mp4", seconds=1)
    video_bytes = video.read_bytes()
    video.unlink()
    path = make_jpeg(library / "MVIMG_1.jpg", size=(320, 240))
    write_tags(path, **{"XMP-GCamera:MicroVideo": "1", "XMP-GCamera:MicroVideoVersion": "1",
                        "XMP-GCamera:MicroVideoOffset": str(len(video_bytes))})
    with path.open("ab") as fh:
        fh.write(video_bytes)
    (pid,) = run(ctx)
    assert photo(ctx, pid)["type"] == "live"
    sha1 = ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (pid,))
    extracted = ctx.sidecar_dir / f"{sha1}.mp4"
    assert extracted.exists() and extracted.read_bytes() == video_bytes


def test_animated_gif_is_detected(ctx, library):
    frames = [Image.new("RGB", (20, 20), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(library / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    (pid,) = run(ctx)
    assert photo(ctx, pid)["type"] == "animated"


def test_corrupt_image_does_not_crash_and_logs_error(ctx, library):
    (library / "broken.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"garbage" * 50)
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors") >= 1
