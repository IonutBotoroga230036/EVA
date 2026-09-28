// The E.V.A. voice protocol (docs/VOICE_PROTOCOL.md) over one WebSocket: text turns, her voice, your mic.
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:web_socket_channel/io.dart';

class EvaEvent {
  final String type;
  final Map<String, dynamic> data;
  EvaEvent(this.type, this.data);
}

class EvaClient {
  IOWebSocketChannel? _ch;
  final _events = StreamController<EvaEvent>.broadcast();
  Stream<EvaEvent> get events => _events.stream;
  bool connected = false;
  String tts = 'browser';
  String stt = 'browser';

  /// https://<pc>.<tailnet>.ts.net (Tailscale, anywhere) or https://192.168.x.x:8443 (home Wi-Fi, needs the
  /// pinned certificate). Plain http is refused: the token must never travel unencrypted.
  static Uri wsUri(String base) {
    final u = Uri.parse(base.trim());
    if (u.scheme != 'https' && u.scheme != 'wss') {
      throw const FormatException('Use an https:// address (Tailscale or port 8443).');
    }
    return u.replace(scheme: 'wss', path: '/ws', query: null);
  }

  Future<void> connect({required String baseUrl, required String token, Uint8List? pinnedCa}) async {
    await close();
    final ctx = SecurityContext(withTrustedRoots: true);            // Tailscale: a real certificate
    if (pinnedCa != null && pinnedCa.isNotEmpty) {
      ctx.setTrustedCertificatesBytes(pinnedCa);                     // home Wi-Fi: E.V.A.'s own CA
    }
    final http = HttpClient(context: ctx)..connectionTimeout = const Duration(seconds: 8);
    final ch = IOWebSocketChannel.connect(wsUri(baseUrl),
        headers: {'Authorization': 'Bearer $token'},
        customClient: http,
        pingInterval: const Duration(seconds: 20));
    await ch.ready;
    _ch = ch;
    connected = true;
    ch.stream.listen((msg) {
      if (msg is! String) return;
      final m = jsonDecode(msg) as Map<String, dynamic>;
      final t = (m['type'] ?? '') as String;
      if (t == 'hello') tts = (m['tts'] ?? 'browser') as String;
      if (t == 'stt') stt = (m['engine'] ?? 'browser') as String;
      _events.add(EvaEvent(t, m));
    }, onDone: () {
      connected = false;
      _events.add(EvaEvent('closed', const {}));
    }, onError: (Object e) {
      connected = false;
      _events.add(EvaEvent('error', {'error': '$e'}));
    });
  }

  void _send(Map<String, dynamic> m) => _ch?.sink.add(jsonEncode(m));
  void say(String text) => _send({'type': 'message', 'text': text, 'voice': false});
  void stop() => _send({'type': 'stop'});
  void arm(int ms) => _send({'type': 'listen', 'arm': ms});
  void wake(bool on) => _send({'type': 'listen', 'wake': on});
  void speaking(bool on) => _send({'type': 'speaking', 'on': on});
  void audio(Uint8List pcm16) => _ch?.sink.add(pcm16);          // binary frame, PCM16 LE mono 16 kHz

  Future<void> close() async {
    final ch = _ch;
    _ch = null;
    connected = false;
    if (ch != null) await ch.sink.close();
  }
}

/// Home Wi-Fi only: download E.V.A.'s CA once, show its fingerprint, pin it after you compare.
Future<Uint8List> fetchCa(String pcAddress) async {
  final host = pcAddress.trim().replaceAll(RegExp(r'^https?://'), '').split(':').first.split('/').first;
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 6);
  try {
    final req = await client.getUrl(Uri.parse('http://$host:8001/eva-ca.crt'));
    final res = await req.close();
    if (res.statusCode != 200) throw HttpException('HTTP ${res.statusCode}');
    final bytes = await res.fold<List<int>>(<int>[], (a, b) => a..addAll(b));
    return Uint8List.fromList(bytes);
  } finally {
    client.close(force: true);
  }
}

/// The same short fingerprint E.V.A. prints in her terminal: SHA-256 of the DER, first 8 bytes.
String caFingerprint(Uint8List pem) {
  final body = utf8.decode(pem).replaceAll(RegExp(r'-----[^-]+-----'), '').replaceAll(RegExp(r'\s'), '');
  final hex = sha256.convert(base64.decode(body)).toString().toUpperCase();
  final pairs = [for (var i = 0; i < 16; i += 2) hex.substring(i, i + 2)];
  return '${pairs.join(':')}...';
}
