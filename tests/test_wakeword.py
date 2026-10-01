"""v0.3 9b: E.V.A.'s own wake word. The real openWakeWord feature models are used; the "voice" is synthetic
(rising chirps play "Eva", everything else plays other speech), so the whole pipeline runs without recordings."""

import json
import random
import wave
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("onnx")
from core.wakeword import features as wf, train as wt

SR = 16000


def chirp(f0, f1, secs=0.55, amp=9000, seed=0):
    t = np.arange(int(secs * SR)) / SR
    phase = 2 * np.pi * (f0 * t + (f1 - f0) * t * t / (2 * secs))
    x = np.sin(phase) * amp * np.hanning(len(t))
    x += np.random.default_rng(seed).normal(0, 150, len(t))
    return x.astype(np.int16)


def write(path: Path, audio: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    pad = np.zeros(SR // 2, np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.concatenate([pad, audio, pad]).tobytes())


def test_features_match_openwakeword_exactly_with_the_shipped_models():
    oww = pytest.importorskip("openwakeword.utils")
    d = wf.feature_model_dir()
    ref = oww.AudioFeatures(str(d / "melspectrogram.onnx"), str(d / "embedding_model.onnx"))
    x = (np.random.default_rng(1).uniform(-1, 1, 32000) * 4000).astype(np.int16)
    assert np.abs(ref._get_embeddings(x) - wf.Featurizer().embeddings(x)).max() == 0.0


def test_a_different_embedding_model_is_refused(tmp_path):
    import shutil
    for n in wf.SHA256:
        shutil.copy(wf.MODELS / n, tmp_path / n)
    (tmp_path / "embedding_model.onnx").write_bytes(b"not the same model")
    with pytest.raises(ValueError, match="isn't the openWakeWord v0.5.1 file"):
        wf.feature_model_dir(tmp_path)


def test_two_seconds_is_one_classifier_input():
    f = wf.Featurizer()
    assert f.embeddings(np.zeros(32000, np.int16)).shape == (16, 96)
    assert f.clip_features(np.zeros(8000, np.int16)).shape == (16, 96)          # short clips padded in front


def test_trim_finds_the_word_and_place_puts_it_near_the_end():
    word = chirp(500, 1500)
    clip = np.concatenate([np.zeros(12000, np.int16), word, np.zeros(9000, np.int16)])
    t = wt.trim(clip)
    assert 0.9 * len(word) <= len(t) <= len(word) + 7 * 320
    placed = wt.place(t, random.Random(0))
    loud = np.where(np.abs(placed) > 2000)[0]
    assert len(placed) == 32000 and 1.2 * SR < loud[-1] < 2 * SR


def test_augment_keeps_length_and_changes_the_audio():
    c = wt.place(chirp(500, 1500), random.Random(1))
    a = wt.augment(c, random.Random(2))
    assert len(a) == len(c) and a.dtype == np.int16 and not np.array_equal(a, c)


def test_the_classifier_learns_and_its_onnx_gives_the_same_scores(tmp_path):
    import onnxruntime as ort
    rng = np.random.default_rng(0)
    xp = rng.normal(1.0, 1.0, (200, 16, 96)).astype(np.float32)
    xn = rng.normal(-1.0, 1.0, (400, 16, 96)).astype(np.float32)
    m = wt.MLP().fit(np.concatenate([xp, xn]), np.r_[np.ones(200), np.zeros(400)].astype(np.float32), epochs=15,
                     log=lambda *_: None)
    assert m.predict(xp[:20]).min() > 0.9 and m.predict(xn[:20]).max() < 0.1
    m.export(tmp_path / "m.onnx")
    s = ort.InferenceSession(str(tmp_path / "m.onnx"), providers=["CPUExecutionProvider"])
    assert s.get_inputs()[0].shape == [1, 16, 96] and s.get_outputs()[0].shape == [1, 1]   # openWakeWord format
    onnx_score = float(s.run(None, {"x": xp[:1]})[0][0, 0])
    assert abs(onnx_score - float(m.predict(xp[:1])[0])) < 1e-4


def test_threshold_never_lets_a_held_back_negative_fire():
    assert wt.choose_threshold(np.array([0.9, 0.95]), np.array([0.3, 0.62])) == 0.67
    assert wt.choose_threshold(np.array([0.9]), np.array([0.1])) == 0.5


@pytest.fixture
def trained(tmp_path, monkeypatch):
    samples = tmp_path / "samples"
    for i in range(12):
        write(samples / "eva" / f"eva_{i:03d}.wav", chirp(450 + 10 * i, 1600, seed=i))
        write(samples / "hey_eva" / f"hey_eva_{i:03d}.wav", chirp(400 + 10 * i, 1500, secs=0.7, seed=50 + i))
    for i in range(8):
        write(samples / "other" / f"other_{i:03d}.wav", chirp(1600, 450 + 20 * i, seed=100 + i))
        write(samples / "speech" / f"speech_{i:03d}.wav",
              (np.random.default_rng(200 + i).normal(0, 3000, int(0.8 * SR))).astype(np.int16))
    monkeypatch.setattr(wf, "MODEL", tmp_path / "eva.onnx")
    monkeypatch.setattr(wf, "INFO", tmp_path / "eva.json")
    report = wt.train(samples=samples, out=wf.MODEL, info=wf.INFO, use_kokoro=False, epochs=25, aug=4,
                      log=lambda *_: None)
    return report, tmp_path


def test_training_end_to_end_with_honest_numbers(trained):
    report, tmp = trained
    assert (tmp / "eva.onnx").exists() and json.loads((tmp / "eva.json").read_text(encoding="utf-8")) == report
    assert report["held_back_eva_clips"] >= 2 and report["recognised"] == report["held_back_eva_clips"]
    assert report["false_wakes"] == 0 and 0.5 <= report["threshold"] <= 0.95
    d = wf.Detector(tmp / "eva.onnx")
    yes = np.concatenate([np.zeros(12000, np.int16), chirp(520, 1600, seed=999), np.zeros(6000, np.int16)])
    no = np.concatenate([np.zeros(12000, np.int16), chirp(1600, 500, seed=998), np.zeros(6000, np.int16)])
    assert d.check(yes)["detected"] and not d.check(no)["detected"]


def test_too_few_clips_is_a_clear_error(tmp_path):
    write(tmp_path / "s" / "eva" / "eva_001.wav", chirp(500, 1500))
    with pytest.raises(ValueError, match="record at least 6"):
        wt.train(samples=tmp_path / "s", out=tmp_path / "m.onnx", info=tmp_path / "m.json", use_kokoro=False,
                 log=lambda *_: None)


def test_test_endpoint_and_status(trained, monkeypatch):
    import io
    import interfaces.web.server_stream as srv
    from fastapi.testclient import TestClient
    report, tmp = trained
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    monkeypatch.setattr(srv, "WAKE_DIR", tmp / "samples")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.concatenate([np.zeros(12000, np.int16), chirp(520, 1600, seed=5),
                                      np.zeros(6000, np.int16)]).tobytes())
    with TestClient(srv.app) as tc:
        st = tc.get("/api/wakeword/status").json()
        assert st["model"]["exists"] and st["model"]["threshold"] == report["threshold"]
        assert st["counts"] == {"eva": 12, "hey_eva": 12, "other": 8, "speech": 8}
        r = tc.post("/api/wakeword/test", content=buf.getvalue()).json()
        assert r["detected"] is True and r["score"] >= r["threshold"]


def test_no_model_yet_says_how_to_train(tmp_path, monkeypatch):
    import interfaces.web.server_stream as srv
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wf, "MODEL", tmp_path / "none.onnx")
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc:
        r = tc.post("/api/wakeword/test", content=b"RIFF" + b"\0" * 2000)
        assert r.status_code == 409 and "python -m core.wakeword.train" in r.json()["detail"]


def test_the_page_asks_for_everyday_sentences_and_can_test():
    html = (Path(__file__).resolve().parents[1] / "interfaces/web/wakeword.html").read_text(encoding="utf-8")
    assert "speech: 20" in html and "id=\"testCard\"" in html and "/api/wakeword/test" in html
