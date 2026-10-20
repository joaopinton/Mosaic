from pathlib import Path

from conftest import make_video, needs_ffmpeg
from PIL import Image

from mosaic.meta.extract import MotionInfo
from mosaic.video import live, poster, probe, transcode
from mosaic.video.probe import VideoInfo


def info(**kw) -> VideoInfo:
    base = dict(duration=3, width=1920, height=1080, fps=30, vcodec="h264", acodec="aac", container="mov,mp4",
                bitrate=8_000_000, pix_fmt="yuv420p")
    return VideoInfo(**{**base, **kw})


def test_web_playable_rules():
    assert transcode.is_web_playable(info(), ".mp4")
    assert transcode.is_web_playable(info(), ".MOV")
    assert not transcode.is_web_playable(info(vcodec="hevc"), ".mp4")
    assert not transcode.is_web_playable(info(acodec="ac3"), ".mp4")
    assert not transcode.is_web_playable(info(pix_fmt="yuv422p10le"), ".mp4")
    assert not transcode.is_web_playable(info(bitrate=80_000_000), ".mp4")
    assert not transcode.is_web_playable(info(vcodec="h264"), ".mkv")
    assert transcode.is_web_playable(info(vcodec="vp9", acodec="opus"), ".webm")
    assert not transcode.is_web_playable(info(vcodec="mpeg4"), ".webm")


def test_poster_time_is_a_tenth_capped_at_ten_seconds():
    assert poster.poster_time(0) == 0
    assert poster.poster_time(20) == 2
    assert poster.poster_time(600) == 10


def test_live_pair_threshold():
    assert live.is_live_pair(3.0) and live.is_live_pair(3.1)
    assert not live.is_live_pair(3.2) and not live.is_live_pair(0)


def test_transcode_command_scales_and_enables_faststart():
    cmd = transcode.build_command(Path("ffmpeg"), Path("in.avi"), Path("out.mp4"))
    assert "+faststart" in cmd and "libx264" in cmd and any("min(1920" in part for part in cmd)
    assert cmd.index("0:a:0?") > cmd.index("-map")


@needs_ffmpeg
def test_probe_reads_stream_properties(tmp_path):
    video = make_video(tmp_path / "v.mp4", seconds=2, size="320x240")
    got = probe.probe(video)
    assert (got.width, got.height, got.vcodec) == (320, 240, "h264")
    assert 1.5 < got.duration < 2.6 and got.fps == 10 and got.acodec == ""


@needs_ffmpeg
def test_probe_returns_none_for_non_video(tmp_path):
    junk = tmp_path / "x.mp4"
    junk.write_bytes(b"not a video")
    assert probe.probe(junk) is None
    assert probe.probe(tmp_path / "missing.mp4") is None


@needs_ffmpeg
def test_poster_is_a_valid_jpeg(tmp_path):
    video = make_video(tmp_path / "v.mp4", seconds=2)
    out = tmp_path / "poster.jpg"
    assert poster.extract_poster(video, out, duration=2.0)
    with Image.open(out) as img:
        assert img.size == (160, 120) and img.format == "JPEG"
    assert not poster.extract_poster(tmp_path / "nope.mp4", tmp_path / "p2.jpg")


@needs_ffmpeg
def test_non_web_video_is_transcoded_once_to_h264_faststart(ctx, tmp_path):
    src = make_video(tmp_path / "old.mp4", seconds=2, codec="mpeg4", size="320x240")
    assert not transcode.is_web_playable(probe.probe(src), ".mp4")
    out = transcode.transcode(ctx, src, "abc123", duration=2)
    assert out and out.exists()
    result = probe.probe(out)
    assert result.vcodec == "h264" and result.pix_fmt == "yuv420p" and (result.width, result.height) == (320, 240)
    assert transcode.is_web_playable(result, out.suffix)
    stamp = out.stat().st_mtime_ns
    assert transcode.transcode(ctx, src, "abc123", duration=2) == out and out.stat().st_mtime_ns == stamp


@needs_ffmpeg
def test_transcode_failure_returns_none_and_leaves_no_partial_file(ctx, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"garbage" * 100)
    assert transcode.transcode(ctx, broken, "bad1") is None
    assert not list(ctx.video_dir.glob("*"))


@needs_ffmpeg
def test_large_video_is_scaled_down_to_1920(ctx, tmp_path):
    src = make_video(tmp_path / "big.mp4", seconds=1, codec="mpeg4", size="2560x1440")
    out = transcode.transcode(ctx, src, "big1", duration=1)
    result = probe.probe(out)
    assert (result.width, result.height) == (1920, 1080)


def test_motion_video_extraction_validates_and_bounds(ctx, tmp_path):
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"V" * 50
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"\xff\xd8" + b"J" * 100 + mp4)
    out = live.extract_motion_video(ctx, photo, "sha-ok", MotionInfo("micro", offset_from_end=len(mp4)))
    assert out and out.read_bytes() == mp4
    assert live.extract_motion_video(ctx, photo, "sha-bad", MotionInfo("micro", offset_from_end=photo.stat().st_size + 5)) is None
    assert live.extract_motion_video(ctx, photo, "sha-garbage", MotionInfo("container", offset_from_end=20)) is None
    assert live.extract_motion_video(ctx, photo, "sha-zero", MotionInfo("micro", offset_from_end=0)) is None
