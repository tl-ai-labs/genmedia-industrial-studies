from conftest import jpeg_with_exif, mp3_with_id3, png_with_text, wav_with_info

from runner.strip import strip_bytes, strip_jpeg, strip_mp3, strip_png, strip_wav


def test_png_drops_text_and_c2pa_keeps_pixels():
    out = strip_png(png_with_text("gemini-3-pro"))
    assert b"gemini-3-pro" not in out
    assert b"tEXt" not in out and b"caBX" not in out
    for keep in (b"IHDR", b"IDAT", b"IEND"):
        assert keep in out
    assert out.startswith(b"\x89PNG")


def test_jpeg_drops_exif_and_comment_keeps_scan():
    out = strip_jpeg(jpeg_with_exif("gpt-image-2"))
    assert b"gpt-image-2" not in out
    assert b"JFIF" in out                     # APP0 stays
    assert b"\xff\xdb" in out                 # DQT stays
    assert out.endswith(b"\xff\xd9")


def test_wav_drops_list_info_keeps_fmt_and_data():
    src = wav_with_info("elevenlabs")
    out = strip_wav(src)
    assert b"elevenlabs" not in out and b"LIST" not in out
    assert b"fmt " in out and b"data" in out
    assert out[:4] == b"RIFF" and out[8:12] == b"WAVE"
    # RIFF size field is consistent with the new body
    import struct
    assert struct.unpack("<I", out[4:8])[0] == len(out) - 8


def test_mp3_drops_id3v2_and_v1_keeps_frames():
    out = strip_mp3(mp3_with_id3("elevenlabs-multilingual-v2"))
    assert b"elevenlabs" not in out
    assert not out.startswith(b"ID3") and not out.endswith(b"TAG" + b"\x00" * 0) or b"TAG" not in out[-128:]
    assert out.startswith(b"\xff\xfb")


def test_unknown_suffix_passes_through():
    data = b"\x1aE\xdf\xa3 webm omni"
    out, stripped = strip_bytes(data, ".webm")
    assert out == data and stripped is False


def test_non_matching_magic_is_untouched():
    assert strip_png(b"not a png") == b"not a png"
    assert strip_jpeg(b"not a jpeg") == b"not a jpeg"
    assert strip_wav(b"RIFF....AVI ") == b"RIFF....AVI "


def test_mp4_udta_is_neutralised_in_place_offsets_kept():
    from conftest import mp4_stub
    from runner.strip import strip_mp4
    src = mp4_stub("omni-flash-vertex", b"the-samples")
    out = strip_mp4(src)
    assert len(out) == len(src)                       # nothing moved
    assert b"omni-flash-vertex" not in out and b"udta" not in out
    assert out.index(b"mdat") == src.index(b"mdat")   # sample offsets still valid
    assert b"the-samples" in out and b"mvhd" in out
    assert strip_mp4(b"not an mp4 at all") == b"not an mp4 at all"
