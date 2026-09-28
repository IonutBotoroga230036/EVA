// Her voice: one WAV per sentence, played strictly in seq order (the server sends them as they're ready).
import 'dart:convert';
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';

class VoiceOut {
  VoiceOut({required this.onSpeaking, required this.onFinished}) {
    _player.onPlayerComplete.listen((_) {
      _playing = false;
      if (_oneShot) {
        _oneShot = false;
        _setSpeaking(false);
        onFinished(oneShot: true);
      } else {
        _pump();
      }
    });
  }

  final void Function(bool on) onSpeaking;
  final void Function({bool oneShot}) onFinished;
  final _player = AudioPlayer();
  final Map<int, Uint8List> _pending = {};
  int _turn = -1, _next = 0;
  int? _total;
  bool _playing = false, _oneShot = false, _speaking = false;

  void _setSpeaking(bool on) {
    if (on == _speaking) return;
    _speaking = on;
    onSpeaking(on);
  }

  void beginTurn(int turn) {
    stop();
    _turn = turn;
    _next = 0;
    _total = null;
  }

  void add(int turn, int seq, String b64) {
    if (turn != _turn) return;                       // a cancelled turn's late sentences are dropped
    _pending[seq] = base64Decode(b64);
    _pump();
  }

  void end(int turn, int count) {
    if (turn != _turn) return;
    _total = count;
    _pump();
  }

  /// The cached "Yes, sir?" after the wake word.
  Future<void> playOnce(Uint8List wav) async {
    stop();
    _oneShot = true;
    _playing = true;
    _setSpeaking(true);
    await _player.play(BytesSource(wav, mimeType: 'audio/wav'));
  }

  void _pump() {
    if (_playing) return;
    final next = _pending.remove(_next);
    if (next != null) {
      _next++;
      _playing = true;
      _setSpeaking(true);
      _player.play(BytesSource(next, mimeType: 'audio/wav'));
      return;
    }
    if (_total != null && _next >= _total!) {
      _total = null;
      _setSpeaking(false);
      onFinished(oneShot: false);
    }
  }

  void duck(bool on) => _player.setVolume(on ? 0.25 : 1.0);

  void stop() {
    _pending.clear();
    _total = null;
    _oneShot = false;
    _player.stop();
    _player.setVolume(1.0);
    _playing = false;
    _setSpeaking(false);
  }

  Future<void> dispose() => _player.dispose();
}
