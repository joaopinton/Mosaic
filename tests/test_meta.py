from mosaic.meta import dates, extract, sources
from mosaic.meta.camera import normalize, upsert_camera, upsert_lens
from mosaic.meta.takeout import parse as parse_takeout


def test_exif_date_without_timezone_keeps_local_time():
    t = dates.parse_exif_datetime("2023:05:01 12:34:56")
    assert (t.utc, t.local, t.tz) == ("2023-05-01T12:34:56Z", "2023-05-01T12:34:56", "")


def test_exif_date_with_offset_converts_to_utc():
    t = dates.parse_exif_datetime("2023:05:01 12:34:56", "+02:00")
    assert (t.utc, t.local, t.tz) == ("2023-05-01T10:34:56Z", "2023-05-01T12:34:56", "+02:00")
    inline = dates.parse_exif_datetime("2023:05:01 00:10:00-05:00")
    assert inline.utc == "2023-05-01T05:10:00Z"


def test_invalid_exif_dates_are_rejected():
    for bad in (None, "", "0000:00:00 00:00:00", "2023:13:40 99:00:00", "boh"):
        assert dates.parse_exif_datetime(bad) is None


def test_filename_dates():
    assert dates.parse_filename_datetime("IMG_20230501_123456.jpg").local == "2023-05-01T12:34:56"
    assert dates.parse_filename_datetime("Foto 2021-07-04.png").local == "2021-07-04T12:00:00"
    assert dates.parse_filename_datetime("Padova Moto Days 0001-.jpg") is None
    assert dates.parse_filename_datetime("IMG_20231399_999999.jpg") is None


def test_extract_basic_fields():
    meta = extract.extract({
        "DateTimeOriginal": "2023:05:01 12:34:56", "OffsetTimeOriginal": "+02:00", "Make": "NIKON CORPORATION",
        "Model": "NIKON D850", "ISO": 200, "ExposureTime": 0.004, "FNumber": 2.8, "FocalLength": 85.0,
        "GPSLatitude": 45.4, "GPSLongitude": 11.8, "ImageWidth": 6000, "ImageHeight": 4000, "Orientation": 6,
        "Subject": ["Moto", "ducati"], "Rating": 4, "DocumentID": "xmp.did:1", "LensModel": "AF-S 85mm",
    }, "x.jpg", 0)
    assert meta.taken.utc == "2023-05-01T10:34:56Z" and meta.taken_src == sources.META
    assert (meta.exposure, meta.focal_length, meta.iso) == ("1/250", 85, 200)
    assert (meta.width, meta.height) == (4000, 6000)  # orientamento 6 → ribaltati
    assert meta.keywords == ("moto", "ducati") and meta.rating == 4 and meta.uuid == "xmp.did:1"
    assert (meta.lat, meta.lng) == (45.4, 11.8)


def test_extract_gps_zero_and_out_of_range_are_dropped():
    assert extract.extract({"GPSLatitude": 0, "GPSLongitude": 0}).lat is None
    assert extract.extract({"GPSLatitude": 95, "GPSLongitude": 10}).lat is None


def test_extract_date_fallback_chain():
    assert extract.extract({}, "IMG_20200102_030405.jpg", 0).taken_src == sources.NAME
    assert extract.extract({"ModifyDate": "2022:01:01 10:00:00"}, "x.jpg", 0).taken_src == sources.MODIFIED
    assert extract.extract({}, "x.jpg", 1_700_000_000).taken_src == sources.AUTO
    assert extract.extract({}, "x.jpg", 0).taken is None


def test_junk_description_is_ignored():
    assert extract.extract({"ImageDescription": "OLYMPUS DIGITAL CAMERA"}).caption == ""
    assert extract.extract({"ImageDescription": "Tramonto"}).caption == "Tramonto"


def test_identifier_like_titles_are_not_titles():
    for junk in ("151419772", "IMG_20230501", "DSC0042", "3f2b8c1e-aaaa-bbbb-cccc-1234567890ab", "pxl_12345"):
        assert extract.extract({"Title": junk}).title == "", junk
    assert extract.extract({"Title": "Tramonto sul Brenta"}).title == "Tramonto sul Brenta"
    assert extract.extract({"Title": "Maggio 2024"}).title == "Maggio 2024"


def test_motion_photo_detection():
    micro = extract.extract({"MicroVideo": 1, "MicroVideoOffset": 1234}).motion
    assert micro.kind == "micro" and micro.offset_from_end == 1234
    container = extract.extract({"MotionPhoto": 1, "DirectoryItemMime": ["image/jpeg", "video/mp4"],
                                 "DirectoryItemLength": [0, 999]}).motion
    assert container.kind == "container" and container.offset_from_end == 999
    assert extract.extract({"EmbeddedVideoFile": "(Binary data)"}).motion.kind == "binary"
    assert extract.extract({}).motion is None


def test_mwg_regions_convert_center_to_top_left():
    meta = extract.extract({"RegionName": ["Anna"], "RegionType": ["Face"], "RegionAreaX": [0.5],
                            "RegionAreaY": [0.5], "RegionAreaW": [0.2], "RegionAreaH": [0.4]})
    region = meta.regions[0]
    assert region.name == "Anna" and round(region.x, 3) == 0.4 and round(region.y, 3) == 0.3


def test_camera_normalization(ctx):
    assert normalize("NIKON CORPORATION", "NIKON D850") == ("Nikon", "D850")
    assert normalize("Canon", "Canon EOS R5") == ("Canon", "EOS R5")
    assert normalize("", "") == ("", "")
    conn = ctx.db.conn()
    first = upsert_camera(conn, "NIKON CORPORATION", "NIKON D850")
    assert upsert_camera(conn, "Nikon", "D850") == first
    assert upsert_camera(conn, None, None) is None
    assert upsert_lens(conn, "AF-S 85mm") == upsert_lens(conn, " AF-S 85mm ")
    assert upsert_lens(conn, "unknown") is None


def _photo(ctx) -> int:
    return ctx.db.conn().execute(
        "INSERT INTO photos(uid, created_at, updated_at) VALUES ('p1','2024-01-01','2024-01-01')").lastrowid


def test_lower_priority_source_never_overwrites_higher(ctx):
    pid = _photo(ctx)
    conn = ctx.db.conn()
    assert sources.set_text(conn, pid, "title", "Da XMP", sources.XMP)
    assert not sources.set_text(conn, pid, "title", "Dall'LLM", sources.LLM)
    assert ctx.db.scalar("SELECT title FROM photos WHERE id=?", (pid,)) == "Da XMP"
    assert sources.set_text(conn, pid, "title", "Manuale", sources.MANUAL)
    assert not sources.set_text(conn, pid, "title", "Di nuovo XMP", sources.XMP)
    assert sources.set_text(conn, pid, "title", "Manuale 2", sources.MANUAL)  # stessa priorità: si aggiorna
    assert not sources.set_text(conn, pid, "caption", "   ", sources.MANUAL)


def test_set_taken_respects_priority(ctx):
    pid = _photo(ctx)
    conn = ctx.db.conn()
    assert sources.set_taken(conn, pid, "2023-01-01T00:00:00Z", "2023-01-01T00:00:00", "", sources.META)
    assert not sources.set_taken(conn, pid, "2020-01-01T00:00:00Z", "2020-01-01T00:00:00", "", sources.NAME)
    assert ctx.db.scalar("SELECT taken_at FROM photos WHERE id=?", (pid,)) == "2023-01-01T00:00:00Z"


def test_takeout_json(tmp_path):
    good = tmp_path / "a.jpg.json"
    good.write_text('{"title":"a.jpg","description":"Mare","photoTakenTime":{"timestamp":"1700000000"},'
                    '"geoData":{"latitude":45.0,"longitude":11.0,"altitude":10}}', encoding="utf-8")
    data = parse_takeout(good)
    assert data.taken.utc == "2023-11-14T22:13:20Z" and data.lat == 45.0 and data.caption == "Mare"
    assert data.title == ""  # il titolo è il nome del file: ignorato
    other = tmp_path / "x.json"
    other.write_text('{"foo": 1}', encoding="utf-8")
    assert parse_takeout(other) is None
    broken = tmp_path / "b.json"
    broken.write_text("{", encoding="utf-8")
    assert parse_takeout(broken) is None
