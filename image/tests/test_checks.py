import pytest

from runner.image.checks import (check_image, ocr_match, phash_distance,
                                 ssim_outside_region)
from runner.loaders import Scenario
from tests.conftest import blank_png, gradient_png, install_fake_ocr


def scenario(**checks):
    return Scenario(id="t-001", modality="image", task="text_to_image",
                    prompt="p", expected="e",
                    checks={"min_width": 1024, "min_height": 1024,
                            "formats": ["png", "jpeg"], "not_blank": True, **checks})


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def gate(outcome, name):
    return next(g for g in outcome.gates if g["gate"] == name)


def test_valid_image_passes_gates(tmp_path):
    out = check_image(scenario(), write(tmp_path, "a.png", gradient_png()))
    assert out.passed
    assert out.measures["width"] == 1024


def test_blank_image_fails_not_blank(tmp_path):
    out = check_image(scenario(), write(tmp_path, "a.png", blank_png()))
    assert not out.passed
    assert not gate(out, "not_blank")["passed"]


def test_small_image_fails_dimensions(tmp_path):
    out = check_image(scenario(), write(tmp_path, "a.png", gradient_png((256, 256))))
    assert not gate(out, "dimensions")["passed"]


def test_corrupt_file_fails_decode(tmp_path):
    out = check_image(scenario(), write(tmp_path, "a.png", b"not an image"))
    assert not out.passed
    assert out.gates == [out.gates[0]]  # only the decode gate ran
    assert not out.gates[0]["passed"]


def test_ocr_exact_match(monkeypatch, tmp_path):
    install_fake_ocr(monkeypatch, lambda p: ["TRAILHEAD 750", "other noise"])
    _, ratio = ocr_match(write(tmp_path, "a.png", gradient_png()), "TRAILHEAD 750")
    assert ratio == 1.0


def test_ocr_typo_fuzzy(monkeypatch, tmp_path):
    install_fake_ocr(monkeypatch, lambda p: ["TRAILHFAD 750"])
    _, ratio = ocr_match(write(tmp_path, "a.png", gradient_png()), "TRAILHEAD 750")
    assert 0.6 < ratio < 1.0


def test_ocr_absent(monkeypatch, tmp_path):
    install_fake_ocr(monkeypatch, lambda p: [])
    found, ratio = ocr_match(write(tmp_path, "a.png", gradient_png()), "TRAILHEAD 750")
    assert ratio == 0.0 and found == ""


def test_ocr_list_matches_each_string_independently(monkeypatch, tmp_path):
    # UI labels interleaved with placeholder text must not be penalised
    install_fake_ocr(monkeypatch, lambda p: [
        "TIDEMARK", "Email", "example@email.com", "Password",
        "Enter your password", "Sign in", "Forgot password?"])
    out = check_image(
        scenario(must_read_text=["TIDEMARK", "Email", "Password",
                                 "Sign in", "Forgot password"]),
        write(tmp_path, "a.png", gradient_png()))
    assert out.measures["ocr_match"] == 1.0
    assert all(v == 1.0 for v in out.measures["ocr_match_per_item"].values())

    # a missing label drags the mean down but not to zero
    install_fake_ocr(monkeypatch, lambda p: ["TIDEMARK", "Email", "Password"])
    out = check_image(
        scenario(must_read_text=["TIDEMARK", "Email", "Password",
                                 "Sign in", "Forgot password"]),
        write(tmp_path, "b.png", gradient_png()))
    assert 0.4 < out.measures["ocr_match"] < 0.9


def test_ocr_measure_is_not_a_gate(monkeypatch, tmp_path):
    # a wrong label is a scored failure, not an invalid file
    install_fake_ocr(monkeypatch, lambda p: ["WRONG WORDS"])
    out = check_image(scenario(must_read_text="TRAILHEAD 750"),
                      write(tmp_path, "a.png", gradient_png()))
    assert out.passed
    assert out.measures["ocr_match"] < 0.5


def test_phash_identical_and_different(tmp_path):
    a = write(tmp_path, "a.png", gradient_png(phase=0))
    b = write(tmp_path, "b.png", gradient_png(phase=0))
    c = write(tmp_path, "c.png", blank_png())
    assert phash_distance(a, b) == 0
    assert phash_distance(a, c) > 12


def test_ssim_requires_declared_region(tmp_path):
    a = write(tmp_path, "a.png", gradient_png())
    b = write(tmp_path, "b.png", gradient_png())
    with pytest.raises(ValueError, match="mask or bbox"):
        ssim_outside_region(a, b)  # no auto-segmentation, ever


def test_ssim_outside_bbox(tmp_path):
    import io
    import numpy as np
    from PIL import Image

    base = np.array(Image.open(io.BytesIO(gradient_png((256, 256)))))
    edited = base.copy()
    edited[64:128, 64:128] = 255  # edit strictly inside the declared bbox

    a = tmp_path / "src.png"
    b = tmp_path / "res.png"
    Image.fromarray(base).save(a)
    Image.fromarray(edited).save(b)

    inside_only = ssim_outside_region(a, b, bbox=[60, 60, 132, 132])
    assert inside_only > 0.99          # untouched outside the region
    global_repaint = ssim_outside_region(a, tmp_path / "res.png", bbox=[0, 0, 8, 8])
    assert global_repaint < inside_only
