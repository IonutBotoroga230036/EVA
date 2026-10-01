"""
openWakeWord's feature front end, reimplemented (and tested number-for-number against openWakeWord 0.4.0):

    16 kHz int16 audio -> melspectrogram.onnx -> x/10 + 2 -> 76-frame windows every 8 frames
                       -> embedding_model.onnx -> one 96-value embedding per 80 ms

A classifier looks at the last 16 embeddings ([1, 16, 96], about 1.3 s of speech) and gives a score 0..1.
A 2-second clip gives exactly 16 embeddings. The same three ONNX files run on the PC and, next round, the phone.

The two feature models (Apache-2.0, openWakeWord release v0.5.1) ship with E.V.A. in assets/wakeword, pinned
by SHA-256: training on the PC and detection on the phone must use the SAME pair (other versions of the embedding
model give very different numbers, and a classifier trained on one is useless with another).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

MODELS = Path(__file__).resolve().parents[2] / "assets" / "wakeword"
SHA256 = {"melspectrogram.onnx": "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
          "embedding_model.onnx": "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f"}
MODEL = Path("data/wakeword/eva.onnx")
INFO = Path("data/wakeword/eva.json")
SR = 16000
CLIP = 2 * SR                  # 2 s of audio -> 16 embeddings -> one classifier input
FRAMES = 16


def feature_model_dir(d: Path = MODELS) -> Path:
    """The shipped pair, verified: a different embedding model would silently break every trained model."""
    import hashlib
    for name, digest in SHA256.items():
        p = d / name
        if not p.exists():
            raise FileNotFoundError(f"{p} is missing; it ships with E.V.A. (assets/wakeword). Re-apply the update.")
        if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError(f"{p} isn't the openWakeWord v0.5.1 file E.V.A. was built for; restore it from the update.")
    return d


def _session(path: Path):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.inter_op_num_threads = so.intra_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])


class Featurizer:
    def __init__(self, model_dir: Optional[Path] = None):
        d = model_dir or feature_model_dir()
        self.mel = _session(d / "melspectrogram.onnx")
        self.emb = _session(d / "embedding_model.onnx")

    def melspectrogram(self, audio: np.ndarray) -> np.ndarray:
        x = np.asarray(audio, dtype=np.int16).astype(np.float32)[None, :]
        spec = np.squeeze(self.mel.run(None, {"input": x})[0])
        return spec / 10 + 2

    def embeddings(self, audio: np.ndarray) -> np.ndarray:
        """(N, 96): one embedding per 8 mel frames (80 ms), over 76-frame windows."""
        spec = self.melspectrogram(audio)
        windows = [spec[i:i + 76] for i in range(0, spec.shape[0], 8) if spec[i:i + 76].shape[0] == 76]
        if not windows:
            return np.zeros((0, 96), dtype=np.float32)
        batch = np.expand_dims(np.array(windows), axis=-1).astype(np.float32)
        return self.emb.run(None, {"input_1": batch})[0].reshape(len(windows), 96)

    def clip_features(self, audio: np.ndarray) -> np.ndarray:
        """(16, 96) for one classifier input: the last 2 s (padded with silence in front if shorter)."""
        a = np.asarray(audio, dtype=np.int16)[-CLIP:]
        if len(a) < CLIP:
            a = np.concatenate([np.zeros(CLIP - len(a), dtype=np.int16), a])
        e = self.embeddings(a)
        return e[-FRAMES:] if len(e) >= FRAMES else np.vstack([np.zeros((FRAMES - len(e), 96), np.float32), e])


class Detector:
    """Scores audio with the trained classifier. For clips and short recordings (the test button, the PC)."""

    def __init__(self, model: Path = MODEL, featurizer: Optional[Featurizer] = None):
        self.feat = featurizer or Featurizer()
        self.clf = _session(model)
        self.input = self.clf.get_inputs()[0].name
        info = json.loads(INFO.read_text(encoding="utf-8")) if INFO.exists() else {}
        self.threshold = float(info.get("threshold", 0.5))

    def scores(self, audio: np.ndarray) -> np.ndarray:
        """One score per 80 ms step, for every full 16-embedding window in the audio."""
        a = np.asarray(audio, dtype=np.int16)
        if len(a) < CLIP:
            a = np.concatenate([np.zeros(CLIP - len(a), dtype=np.int16), a])
        e = self.embeddings_cache = self.feat.embeddings(a)
        if len(e) < FRAMES:
            return np.zeros(0, dtype=np.float32)
        windows = np.stack([e[i:i + FRAMES] for i in range(0, len(e) - FRAMES + 1)]).astype(np.float32)
        return np.array([float(self.clf.run(None, {self.input: w[None]})[0].reshape(-1)[0]) for w in windows])

    def check(self, audio: np.ndarray) -> dict:
        s = self.scores(audio)
        best = float(s.max()) if len(s) else 0.0
        return {"score": round(best, 3), "detected": best >= self.threshold, "threshold": self.threshold}


def read_wav(path: Path) -> np.ndarray:
    import wave
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SR or w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: need 16 kHz 16-bit WAV")
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return a if w.getnchannels() == 1 else a.reshape(-1, w.getnchannels())[:, 0]
