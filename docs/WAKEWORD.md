# E.V.A.'s own wake word (v0.3 milestone 9b)

"Eva" and "Hey Eva", trained on your voice, in openWakeWord's format, so the same small model runs on the PC and
(next round) on your phone with the screen off. Everything stays on your PC.

## 1. Record (done once, about 8 minutes)
Open `/wakeword` on your phone (the mic the model will listen through) and fill all four counters:
15 "Eva", 15 "Hey Eva", 10 look-alike phrases, and 20 everyday sentences without "Eva". The everyday sentences
matter most: they teach the model that your voice alone isn't "Eva".

## 2. Train (about 5-15 minutes)
```powershell
cd "D:\Project E.V.A\eva"
pip install onnx
python -m core.wakeword.train
```
It uses your clips plus Kokoro saying "Eva" and ordinary sentences in many voices (many speakers are what make a
model learn the word instead of the voice), with noise, volume and speed variations. 20% of your real clips are
held back and never trained on; the numbers it prints are measured on those.

`--no-kokoro` trains on your clips only (quicker, less robust). `--synthetic 800` uses more synthetic clips.

## 3. Test
Back on `/wakeword`: the Test card shows the numbers and lets you say "Eva" (should say "Heard Eva") or anything
else (should say "Not Eva"). If it misses you: record 10 more "Eva" clips in the room where it failed and train
again. If it fires on other words: record more everyday sentences and train again.

## Files
- `data/wakeword/samples/` your clips (eva, hey_eva, other, speech)
- `data/wakeword/eva.onnx` the trained classifier; `data/wakeword/eva.json` its threshold and test numbers
- `assets/wakeword/` the two shared openWakeWord feature models (Apache-2.0, v0.5.1), shipped with E.V.A. so the
  PC and the phone always use the same pair.
