# openWakeWord feature models

`melspectrogram.onnx` and `embedding_model.onnx` are the shared feature models of openWakeWord
(https://github.com/dscripka/openWakeWord, release v0.5.1), licensed under the Apache License 2.0.
The embedding model is a re-implementation of Google's speech_embedding model (Apache-2.0).

E.V.A. ships them so the PC (training) and the phone (detection) always use the SAME pair: a wake-word model
trained with one embedding model does not work with another.

SHA-256:
- melspectrogram.onnx   ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f
- embedding_model.onnx  70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f
