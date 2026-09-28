// Your voice, as E.V.A.'s pipeline wants it: PCM16, 16 kHz, mono, with Android's echo cancellation so her
// own voice from the speaker doesn't come back as a command.
import 'dart:async';
import 'dart:typed_data';

import 'package:record/record.dart';

class Mic {
  final _rec = AudioRecorder();
  StreamSubscription<Uint8List>? _sub;
  bool get on => _sub != null;

  Future<bool> start(void Function(Uint8List pcm) onPcm) async {
    if (_sub != null) return true;
    if (!await _rec.hasPermission()) return false;
    final stream = await _rec.startStream(const RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: 16000,
      numChannels: 1,
      echoCancel: true,
      noiseSuppress: true,
      autoGain: true,
    ));
    _sub = stream.listen(onPcm);
    return true;
  }

  Future<void> stop() async {
    await _sub?.cancel();
    _sub = null;
    if (await _rec.isRecording()) await _rec.stop();
  }

  Future<void> dispose() async {
    await stop();
    await _rec.dispose();
  }
}
