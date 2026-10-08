"""
Train E.V.A.'s wake word from your recordings (v0.3 milestone 9b).

    python -m core.wakeword.train                  # your clips + Kokoro voices, about 5-15 minutes
    python -m core.wakeword.train --no-kokoro      # your clips only (quicker, less robust)

What goes in:
    positives   your "Eva" / "Hey Eva" clips, plus Kokoro saying them in all its voices and speeds
    negatives   your look-alike phrases and everyday sentences, Kokoro saying look-alikes and ordinary sentences
                in many voices, and plain noise. Many speakers matter: openWakeWord's embeddings encode WHO
                speaks strongly, so without them a model learns "Ionut's voice" instead of "Eva".
    augmented   volume, background noise at several levels, slight speed changes, different positions.
What comes out:
    data/wakeword/eva.onnx   the classifier, openWakeWord format ([1,16,96] -> [1,1]), for the PC and the phone
    data/wakeword/eva.json   the threshold and honest numbers: measured on your real clips the model never saw

Nothing leaves your PC.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from loguru import logger

from core.wakeword.features import CLIP, INFO, MODEL, SR, Featurizer, read_wav

SAMPLES = Path("data/wakeword/samples")
POSITIVE = ("eva", "hey_eva")
NEGATIVE = ("other", "speech")

EH_VA = "[Eva](/ˈɛvə/)"                 # "EH-va", the Romanian way; plain "Eva" is Kokoro's "EE-vuh"
SAY_POSITIVE = ["Eva", "Eva?", "Hey Eva", "Hey, Eva.", "Okay Eva",
                f"{EH_VA}.", f"{EH_VA}?", f"Hey {EH_VA}", f"Hey, {EH_VA}.", f"Okay {EH_VA}"]
SAY_LOOKALIKE = ["ever", "Ava", "every day", "eleven", "a vase", "Emma", "Eve", "evil", "Evan", "a bar",
                 "heavier", "hey there", "hey Ava", "hey Emma", "never", "ever since", "Elena", "Diva", "Neva"]
SAY_SENTENCES = [
    "what's the weather like tomorrow", "turn the lights red", "play some music", "what time is it",
    "set a timer for ten minutes", "remind me to call my mother", "how are you doing today",
    "I'm going to the store later", "can you check my calendar", "the meeting starts at three",
    "let's get something to eat", "open the window please", "that was a great movie", "send the email now",
    "where did I put my keys", "it's raining again outside", "I need to study tonight", "good morning",
    "what do you think about that", "turn the volume down a little", "we should leave soon",
    "every evening I go for a walk", "never mind, it's fine", "have you seen the news", "call me back later",
]
KOKORO_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_alloy", "af_nova", "af_river",
                 "am_adam", "am_michael", "am_fenrir", "am_puck", "am_echo", "am_eric", "am_liam", "am_onyx",
                 "bf_emma", "bf_isabella", "bf_alice", "bf_lily", "bm_george", "bm_lewis", "bm_daniel", "bm_fable"]


# ------------------------------------------------------------ audio helpers (pure, tested)
def trim(audio: np.ndarray, frame: int = 320, rel: float = 0.12) -> np.ndarray:
    """The spoken part of a clip: frames louder than rel x the loudest frame, with a little margin."""
    a = np.asarray(audio, dtype=np.int16)
    if len(a) < frame * 2:
        return a
    n = len(a) // frame
    rms = np.sqrt((a[: n * frame].astype(np.float32).reshape(n, frame) ** 2).mean(axis=1))
    if rms.max() < 50:
        return a[:0]
    loud = np.where(rms >= rms.max() * rel)[0]
    start, end = max(0, loud[0] - 3) * frame, min(n, loud[-1] + 4) * frame
    return a[start:end]


def place(speech: np.ndarray, rng: random.Random, length: int = CLIP) -> np.ndarray:
    """Put speech in a 2 s window, ending between 1.55 s and 1.95 s (where the classifier looks hardest)."""
    s = np.asarray(speech, dtype=np.int16)[-(length - 800):]
    out = np.zeros(length, dtype=np.int16)
    end = rng.randint(int(1.55 * SR), int(1.95 * SR))
    start = max(0, end - len(s))
    out[start:start + len(s)] = s[: length - start]
    return out


def noise(n: int, rng: random.Random, kind: str = "") -> np.ndarray:
    kind = kind or rng.choice(["white", "pink", "brown"])
    w = np.random.default_rng(rng.randint(0, 2**31)).standard_normal(n)
    if kind == "white":
        x = w
    else:
        f = np.fft.rfft(w)
        freqs = np.maximum(np.fft.rfftfreq(n), 1.0 / n)
        f /= np.sqrt(freqs) if kind == "pink" else freqs
        x = np.fft.irfft(f, n)
    return (x / (np.abs(x).max() + 1e-9)).astype(np.float32)


def augment(clip: np.ndarray, rng: random.Random) -> np.ndarray:
    """Volume (-9..+6 dB), background noise at 5-30 dB SNR (or none), slight speed change (+-7%)."""
    x = clip.astype(np.float32)
    if rng.random() < 0.6:
        speed = rng.uniform(0.93, 1.07)
        idx = np.clip(np.arange(0, len(x), speed), 0, len(x) - 1)
        x = np.interp(idx, np.arange(len(x)), x)
        x = np.pad(x, (max(0, CLIP - len(x)), 0))[-CLIP:]
    x *= 10 ** (rng.uniform(-9, 6) / 20)
    if rng.random() < 0.75:
        p = float(np.mean(x ** 2)) + 1e-6
        snr = rng.uniform(5, 30)
        n = noise(len(x), rng)
        x = x + n * np.sqrt(p / (10 ** (snr / 10)) / (float(np.mean(n ** 2)) + 1e-9))
    return np.clip(x, -32768, 32767).astype(np.int16)


def kokoro_clips(texts: list[str], voices: list[str], speeds=(0.85, 1.0, 1.15),
                 limit: int = 400, log: Callable = logger.info) -> list[np.ndarray]:
    """Kokoro saying each text in many voices, resampled 24 kHz -> 16 kHz. Voices that fail are skipped."""
    try:
        from kokoro import KPipeline
        from scipy.signal import resample_poly
    except Exception as e:
        log(f"WAKEWORD: Kokoro isn't available ({e}); training without synthetic voices")
        return []
    pipes, out = {}, []
    jobs = [(t, v, s) for t in texts for v in voices for s in speeds]
    random.Random(7).shuffle(jobs)
    for text, voice, speed in jobs:
        if len(out) >= limit:
            break
        lang = voice[0]
        try:
            pipe = pipes.get(lang) or pipes.setdefault(lang, KPipeline(lang_code=lang))
            chunks = [np.asarray(a.cpu().numpy() if hasattr(a, "cpu") else a, dtype=np.float32)
                      for _, _, a in pipe(text, voice=voice, speed=speed)]
            if not chunks:
                continue
            a = resample_poly(np.concatenate(chunks), 2, 3)
            out.append(np.clip(a / (np.abs(a).max() + 1e-9) * 20000, -32768, 32767).astype(np.int16))
        except Exception as e:
            log(f"WAKEWORD: voice {voice} skipped ({str(e)[:60]})")
            voices = [v for v in voices if v != voice]
    return out


# ------------------------------------------------------------ the classifier (NumPy, no PyTorch needed)
class MLP:
    """[16, 96] -> standardise -> 64 ReLU units -> 1 sigmoid. Small on purpose: fast on a phone, hard to overfit."""

    def __init__(self, hidden: int = 64, seed: int = 0):
        self.hidden, self.rng = hidden, np.random.default_rng(seed)

    def _init(self, d: int) -> None:
        self.w1 = (self.rng.standard_normal((d, self.hidden)) * np.sqrt(2.0 / d)).astype(np.float32)
        self.b1 = np.zeros(self.hidden, np.float32)
        self.w2 = (self.rng.standard_normal((self.hidden, 1)) * np.sqrt(1.0 / self.hidden)).astype(np.float32)
        self.b2 = np.zeros(1, np.float32)

    def predict(self, x: np.ndarray) -> np.ndarray:
        z = (x.reshape(len(x), -1) - self.mean) / self.std
        h = np.maximum(z @ self.w1 + self.b1, 0)
        return (1 / (1 + np.exp(-np.clip(h @ self.w2 + self.b2, -30, 30)))).reshape(-1)

    def fit(self, x: np.ndarray, y: np.ndarray, epochs: int = 60, lr: float = 1e-3, l2: float = 1e-3,
            log: Callable = logger.info, x_val: Optional[np.ndarray] = None, y_val: Optional[np.ndarray] = None,
            patience: int = 8) -> "MLP":
        """With x_val: stops when the loss on clips it never trains on stops improving, and keeps the best."""
        x = x.reshape(len(x), -1).astype(np.float32)
        self.mean, self.std = x.mean(axis=0), x.std(axis=0) + 1e-3
        z = (x - self.mean) / self.std
        self._init(z.shape[1])
        pos = y.sum()
        w = np.where(y > 0.5, (len(y) - pos) / max(1.0, pos), 1.0).astype(np.float32)   # balance the classes
        params = [self.w1, self.b1, self.w2, self.b2]
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        t = 0
        best, best_params, waited = float("inf"), None, 0
        for ep in range(epochs):
            order = self.rng.permutation(len(z))
            total = 0.0
            for i in range(0, len(z), 256):
                b = order[i:i + 256]
                xb, yb, wb = z[b], y[b], w[b]
                a1 = xb @ self.w1 + self.b1
                h = np.maximum(a1, 0)
                p = 1 / (1 + np.exp(-np.clip(h @ self.w2 + self.b2, -30, 30))).reshape(-1)
                total += float(-(wb * (yb * np.log(p + 1e-7) + (1 - yb) * np.log(1 - p + 1e-7))).sum())
                g = (wb * (p - yb) / len(b))[:, None]                       # d loss / d logit
                gw2, gb2 = h.T @ g + l2 * self.w2, g.sum(axis=0)
                gh = (g @ self.w2.T) * (a1 > 0)
                gw1, gb1 = xb.T @ gh + l2 * self.w1, gh.sum(axis=0)
                t += 1
                for k, (prm, grd) in enumerate(zip(params, [gw1, gb1, gw2, gb2])):   # Adam
                    m[k] = 0.9 * m[k] + 0.1 * grd
                    v[k] = 0.999 * v[k] + 0.001 * grd * grd
                    prm -= lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8)
            if x_val is not None and len(x_val):
                pv = self.predict(x_val)
                vloss = float(-(y_val * np.log(pv + 1e-7) + (1 - y_val) * np.log(1 - pv + 1e-7)).mean())
                if vloss < best - 1e-4:
                    best, waited = vloss, 0
                    best_params = [q.copy() for q in params]
                else:
                    waited += 1
                    if waited >= patience:
                        log(f"WAKEWORD: stopped at epoch {ep + 1}: no better on unseen clips (loss {best:.4f})")
                        break
            if ep % 10 == 0 or ep == epochs - 1:
                log(f"WAKEWORD: epoch {ep + 1}/{epochs}, loss {total / len(z):.4f}")
        if best_params is not None:
            self.w1, self.b1, self.w2, self.b2 = best_params
        return self

    def export(self, path: Path) -> None:
        """openWakeWord format: input x [1,16,96] float32 -> output score [1,1]. Standardisation folded in."""
        import onnx
        from onnx import TensorProto, helper, numpy_helper
        w1 = (self.w1 / self.std[:, None]).astype(np.float32)
        b1 = (self.b1 - (self.mean / self.std) @ self.w1).astype(np.float32)
        init = [numpy_helper.from_array(np.array([-1, 16 * 96], dtype=np.int64), "shape"),
                numpy_helper.from_array(w1, "w1"), numpy_helper.from_array(b1, "b1"),
                numpy_helper.from_array(self.w2.astype(np.float32), "w2"),
                numpy_helper.from_array(self.b2.astype(np.float32), "b2")]
        nodes = [helper.make_node("Reshape", ["x", "shape"], ["flat"]),
                 helper.make_node("MatMul", ["flat", "w1"], ["m1"]), helper.make_node("Add", ["m1", "b1"], ["a1"]),
                 helper.make_node("Relu", ["a1"], ["h"]),
                 helper.make_node("MatMul", ["h", "w2"], ["m2"]), helper.make_node("Add", ["m2", "b2"], ["a2"]),
                 helper.make_node("Sigmoid", ["a2"], ["score"])]
        graph = helper.make_graph(nodes, "eva_wakeword",
                                  [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 16, 96])],
                                  [helper.make_tensor_value_info("score", TensorProto.FLOAT, [1, 1])], init)
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)], producer_name="E.V.A.")
        model.ir_version = 8
        onnx.checker.check_model(model)
        path.parent.mkdir(parents=True, exist_ok=True)
        onnx.save(model, str(path))


def choose_threshold(pos: np.ndarray, neg: np.ndarray, floor: float = 0.5) -> float:
    """The lowest threshold at which none of the held-back negatives fire, with a margin, never below floor."""
    top_neg = float(neg.max()) if len(neg) else 0.0
    return round(min(0.95, max(floor, top_neg + 0.05)), 3)


# ------------------------------------------------------------ the whole job
def train(samples: Path = SAMPLES, out: Path = MODEL, info: Path = INFO, use_kokoro: bool = True,
          synthetic: int = 400, epochs: int = 60, aug: int = 12, folds: int = 5,
          featurizer: Optional[Featurizer] = None, log: Callable = logger.info) -> dict:
    """Every one of your clips is tested once by a model that never saw it (cross-validation), so the numbers
    are about all of them, not a lucky 6. The model that's saved then learns from all of them."""
    t0 = time.time()
    rng = random.Random(42)
    feat = featurizer or Featurizer()

    def load(labels) -> list[tuple[str, np.ndarray]]:
        clips = []
        for lbl in labels:
            for p in sorted((samples / lbl).glob("*.wav")) if (samples / lbl).exists() else []:
                s = trim(read_wav(p))
                if len(s) > SR // 10:
                    clips.append((p.name, s))
        return clips

    real_pos, real_neg = load(POSITIVE), load(NEGATIVE)
    if len(real_pos) < 6:
        raise ValueError(f"only {len(real_pos)} usable Eva clips; record at least 6 at /wakeword")
    log(f"WAKEWORD: {len(real_pos)} of your Eva clips, {len(real_neg)} of your other clips")

    syn_pos = syn_neg = []
    if use_kokoro and synthetic > 0:
        log("WAKEWORD: Kokoro is saying Eva (both EE-va and EH-va) in many voices...")
        syn_pos = [trim(c) for c in kokoro_clips(SAY_POSITIVE, KOKORO_VOICES, limit=synthetic, log=log)]
        log("WAKEWORD: ...and look-alike words and ordinary sentences")
        syn_neg = [trim(c) for c in kokoro_clips(SAY_LOOKALIKE + SAY_SENTENCES, KOKORO_VOICES,
                                                 speeds=(1.0,), limit=synthetic, log=log)]
    log(f"WAKEWORD: {len(syn_pos)} synthetic Eva clips, {len(syn_neg)} synthetic other clips")

    log("WAKEWORD: computing features...")
    def augmented(clip: np.ndarray, copies: int) -> np.ndarray:
        return np.array([feat.clip_features(augment(place(clip, rng), rng)) for _ in range(copies)],
                        dtype=np.float32).reshape(-1, 16, 96)
    pos_aug = [augmented(c, aug) for _, c in real_pos]               # per clip, so folds can leave clips out
    neg_aug = [augmented(c, aug) for _, c in real_neg]
    pos_clean = np.array([feat.clip_features(place(c, rng)) for _, c in real_pos], dtype=np.float32)
    neg_clean = np.array([feat.clip_features(place(c, rng)) for _, c in real_neg], dtype=np.float32) \
        if real_neg else np.zeros((0, 16, 96), np.float32)
    empty = np.zeros((0, 16, 96), np.float32)
    syn_p = np.concatenate([augmented(c, 2) for c in syn_pos]) if syn_pos else empty
    syn_n = np.concatenate([augmented(c, 2) for c in syn_neg]) if syn_neg else empty
    noise_clips = [(noise(CLIP, rng) * rng.uniform(300, 6000)).astype(np.int16) for _ in range(150)]
    always_n = np.concatenate([np.array([feat.clip_features(n) for n in noise_clips], dtype=np.float32),
                               np.array([feat.clip_features(np.zeros(CLIP, np.int16))] * 20, dtype=np.float32)])

    def fit_on(pi: list[int], ni: list[int], quiet: bool) -> "MLP":
        """Train on your clips pi/ni (+ synthetic); 15% of them sit out as the early-stopping check."""
        r = random.Random(len(pi) * 1000 + len(ni))
        vp, vn = set(r.sample(pi, max(1, len(pi) * 15 // 100))), set(r.sample(ni, max(1, len(ni) * 15 // 100)) if ni else [])
        xp = np.concatenate([pos_aug[i] for i in pi if i not in vp] + [syn_p])
        xn = np.concatenate([neg_aug[i] for i in ni if i not in vn] + [syn_n, always_n])
        xv = np.concatenate([pos_clean[sorted(vp)], neg_clean[sorted(vn)]]) if vn else pos_clean[sorted(vp)]
        yv = np.r_[np.ones(len(vp)), np.zeros(len(vn))].astype(np.float32)
        return MLP().fit(np.concatenate([xp, xn]), np.r_[np.ones(len(xp)), np.zeros(len(xn))].astype(np.float32),
                         epochs=epochs, log=(lambda *_: None) if quiet else log, x_val=xv, y_val=yv)

    k = max(2, min(folds, len(real_pos) // 3))
    order_p, order_n = list(range(len(real_pos))), list(range(len(real_neg)))
    rng.shuffle(order_p)
    rng.shuffle(order_n)
    cv_p, cv_n = np.zeros(len(real_pos)), np.zeros(len(real_neg))
    for f in range(k):
        tp_i, tn_i = order_p[f::k], order_n[f::k]
        m = fit_on([i for i in order_p if i not in tp_i], [i for i in order_n if i not in tn_i], quiet=True)
        cv_p[tp_i] = m.predict(pos_clean[tp_i])
        if tn_i:
            cv_n[tn_i] = m.predict(neg_clean[tn_i])
        log(f"WAKEWORD: check {f + 1} of {k} done")
    threshold = choose_threshold(cv_p, cv_n)
    misses = sorted([(real_pos[i][0], round(float(cv_p[i]), 3)) for i in range(len(real_pos)) if cv_p[i] < threshold],
                    key=lambda m: m[1])
    wrong = sorted([(real_neg[i][0], round(float(cv_n[i]), 3)) for i in range(len(real_neg)) if cv_n[i] >= threshold],
                   key=lambda m: -m[1])

    log(f"WAKEWORD: training the final model on all {len(real_pos)} of your Eva clips")
    model = fit_on(order_p, order_n, quiet=False)
    model.export(out)
    report = {"trained": datetime.now().isoformat(timespec="seconds"), "threshold": threshold,
              "held_back_eva_clips": len(real_pos), "recognised": int((cv_p >= threshold).sum()),
              "held_back_other_clips": len(real_neg), "false_wakes": int((cv_n >= threshold).sum()),
              "missed": [f"{n} ({s})" for n, s in misses], "false_wake_clips": [f"{n} ({s})" for n, s in wrong],
              "synthetic_eva": len(syn_pos), "synthetic_other": len(syn_neg), "checks": k,
              "minutes": round((time.time() - t0) / 60, 1)}
    info.write_text(json.dumps(report, indent=1), encoding="utf-8")
    log(f"WAKEWORD: done. Each clip tested by a model that never saw it: recognised {report['recognised']} of "
        f"{len(real_pos)} Eva clips, {report['false_wakes']} false wakes on {len(real_neg)} other clips, "
        f"threshold {threshold}. Saved {out}")
    if misses:
        log("WAKEWORD: missed (listen to these; a clipped or very quiet recording is worth re-recording): "
            + ", ".join(f"{n} ({s})" for n, s in misses))
    if wrong:
        log("WAKEWORD: woke on: " + ", ".join(f"{n} ({s})" for n, s in wrong))
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Train E.V.A.'s wake word from data/wakeword/samples")
    ap.add_argument("--no-kokoro", action="store_true", help="only your recordings (no synthetic voices)")
    ap.add_argument("--synthetic", type=int, default=400, help="synthetic clips per class (default 400)")
    ap.add_argument("--epochs", type=int, default=60)
    a = ap.parse_args()
    train(use_kokoro=not a.no_kokoro, synthetic=a.synthetic, epochs=a.epochs)


if __name__ == "__main__":
    main()
