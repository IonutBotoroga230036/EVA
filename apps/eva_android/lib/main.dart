// E.V.A. for Android (v0.3 milestone 9): talk to E.V.A. on your PC from your phone.
//
// The phone is a microphone, a speaker and a screen. Everything else (listening, Whisper, the brain, Kokoro,
// the safety rules) runs on the PC, over the same protocol as the web interface (docs/VOICE_PROTOCOL.md).
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'eva_client.dart';
import 'mic.dart';
import 'voice_out.dart';

const _violet = Color(0xFF8B5CF6);
const _bg = Color(0xFF0B0814);
const _assistant = MethodChannel('eva/assistant');
const _store = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const EvaApp());
}

class EvaApp extends StatelessWidget {
  const EvaApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'E.V.A.',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          brightness: Brightness.dark,
          scaffoldBackgroundColor: _bg,
          colorScheme: ColorScheme.fromSeed(seedColor: _violet, brightness: Brightness.dark),
          useMaterial3: true,
        ),
        home: const HomeScreen(),
      );
}

class Settings {
  String url, token, ca;
  Settings(this.url, this.token, this.ca);

  static Future<Settings> load() async => Settings(
        await _store.read(key: 'url') ?? '',
        await _store.read(key: 'token') ?? '',
        await _store.read(key: 'ca') ?? '',
      );

  Future<void> save() async {
    await _store.write(key: 'url', value: url);
    await _store.write(key: 'token', value: token);
    await _store.write(key: 'ca', value: ca);
  }

  bool get complete => url.isNotEmpty && token.isNotEmpty;
}

enum Face { idle, listening, thinking, speaking }

class Line {
  final bool eva;
  final String text;
  Line(this.eva, this.text);
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver, SingleTickerProviderStateMixin {
  final client = EvaClient();
  final mic = Mic();
  late final VoiceOut voice;
  late final AnimationController pulse;
  final input = TextEditingController();
  final lines = <Line>[];
  Settings? settings;
  StreamSubscription<EvaEvent>? sub;
  Face face = Face.idle;
  String caption = 'Connecting...';
  String? confirmText;
  Uint8List? wakePhrase;
  bool wakeMode = false, lastWasVoice = false, followUp = true, micDenied = false;
  int followMs = 7000;
  Timer? retry;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    pulse = AnimationController(vsync: this, duration: const Duration(milliseconds: 1400))..repeat(reverse: true);
    voice = VoiceOut(onSpeaking: (on) => client.speaking(on), onFinished: _spoken);
    _assistant.setMethodCallHandler((call) async {
      if (call.method == 'assist') _listen();             // long-press power while E.V.A. is open
    });
    _start();
  }

  Future<void> _start() async {
    settings = await Settings.load();
    if (!settings!.complete) {
      if (mounted) await _openSettings();
      return;
    }
    await _connect();
  }

  Future<void> _connect() async {
    retry?.cancel();
    final s = settings!;
    setState(() => caption = 'Connecting...');
    try {
      await client.connect(
          baseUrl: s.url, token: s.token, pinnedCa: s.ca.isEmpty ? null : Uint8List.fromList(utf8.encode(s.ca)));
    } catch (e) {
      setState(() {
        face = Face.idle;
        caption = 'Can\'t reach E.V.A.: ${_short(e)}';
      });
      retry = Timer(const Duration(seconds: 5), _connect);
      return;
    }
    sub?.cancel();
    sub = client.events.listen(_on);
    await _startMic();
    final byAssist = await _assistant.invokeMethod<bool>('launchedByAssist') ?? false;
    if (byAssist) _listen();                               // opened by long-press power: listen at once
  }

  Future<void> _startMic() async {
    if (client.stt != 'server') return;                    // E.V.A. announces server listening after hello
    final ok = await mic.start(client.audio);
    setState(() => micDenied = !ok);
  }

  String _short(Object e) => '$e'.replaceAll(RegExp(r'^\w+Exception:?\s*'), '').split('\n').first;

  void _on(EvaEvent ev) {
    final d = ev.data;
    switch (ev.type) {
      case 'hello':
        setState(() => caption = 'Tap the orb, or type');
      case 'phrase':
        if (d['key'] == 'wake' && d['audio'] != null) wakePhrase = base64Decode(d['audio'] as String);
      case 'stt':
        if (client.stt == 'server') {
          _startMic();
          if (wakeMode) client.wake(true);
        }
      case 'turn_start':
        voice.beginTurn(d['turn'] as int);
        followUp = true;
        followMs = 7000;
        setState(() {
          face = Face.thinking;
          confirmText = null;
          caption = '';
        });
      case 'ack':
        setState(() => caption = d['text'] as String? ?? '');
      case 'token':
        setState(() => caption += d['text'] as String? ?? '');
      case 'final':
        final text = d['text'] as String? ?? '';
        setState(() {
          caption = text;
          lines.add(Line(true, text));
          if (client.tts != 'kokoro') face = Face.idle;
        });
      case 'widget':
        final w = d['data'] as Map<String, dynamic>? ?? const {};
        if (w['kind'] == 'confirm') setState(() => confirmText = w['text'] as String?);
      case 'audio':
        setState(() => face = Face.speaking);
        voice.add(d['turn'] as int, d['seq'] as int, d['audio'] as String);
      case 'audio_end':
        voice.end(d['turn'] as int, d['count'] as int);
      case 'turn_meta':
        followUp = d['follow_up'] != false;                // music just started: no open mic for lyrics
        followMs = (d['listen_ms'] as int?) ?? 7000;
      case 'heard':
        if (d['stt'] == true) {
          lastWasVoice = true;
          setState(() {
            lines.add(Line(false, d['text'] as String? ?? ''));
            face = Face.thinking;
          });
        }
      case 'listen':
        final st = d['state'];
        setState(() {
          if (st == 'speech') face = Face.listening;
          if (st == 'end') face = Face.thinking;
          if ((st == 'noise' || st == 'timeout') && face != Face.speaking) {
            face = Face.idle;
            caption = 'Tap the orb, or type';
          }
        });
      case 'wake':
        setState(() {
          caption = d['text'] as String? ?? 'Yes, sir?';
          face = Face.listening;
        });
        if (wakePhrase != null) voice.playOnce(wakePhrase!);
      case 'barge_in':
        if (d['stage'] == 'duck') voice.duck(true);
        if (d['stage'] == 'resume') voice.duck(false);
        if (d['stage'] == 'stop') {
          voice.stop();
          setState(() => face = Face.listening);
        }
      case 'closed':
      case 'error':
        mic.stop();
        setState(() {
          face = Face.idle;
          caption = 'Connection lost. Reconnecting...';
        });
        retry = Timer(const Duration(seconds: 3), _connect);
    }
  }

  void _spoken({bool oneShot = false}) {
    if (oneShot) return;                                   // "Yes, sir?": the server already opened a window
    if (lastWasVoice && followUp && mic.on) {
      client.arm(followMs);                                // follow-up without "Eva"
      setState(() => face = Face.listening);
    } else {
      setState(() => face = Face.idle);
    }
  }

  void _listen() {
    if (!client.connected) return;
    HapticFeedback.lightImpact();
    if (face == Face.speaking || face == Face.thinking) {
      client.stop();
      voice.stop();
    }
    if (!mic.on) {
      setState(() => caption = micDenied ? 'Microphone permission is off for E.V.A.' : 'Listening needs E.V.A.\'s server listening on.');
      return;
    }
    client.arm(8000);
    setState(() {
      face = Face.listening;
      caption = 'Listening...';
    });
  }

  void _send([String? text]) {
    final t = (text ?? input.text).trim();
    if (t.isEmpty || !client.connected) return;
    lastWasVoice = false;
    client.say(t);
    input.clear();
    setState(() {
      lines.add(Line(false, t));
      confirmText = null;
    });
  }

  Future<void> _openSettings() async {
    final saved = await Navigator.of(context).push<bool>(
        MaterialPageRoute(builder: (_) => SettingsScreen(settings: settings ?? Settings('', '', ''))));
    if (saved == true) {
      settings = await Settings.load();
      await _connect();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused) {
      mic.stop();                                          // the mic only runs while E.V.A. is on screen
      voice.stop();
    } else if (state == AppLifecycleState.resumed && client.connected) {
      _startMic();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    retry?.cancel();
    sub?.cancel();
    client.close();
    mic.dispose();
    voice.dispose();
    pulse.dispose();
    super.dispose();
  }

  Color get _faceColor => switch (face) {
        Face.listening => const Color(0xFF6EE7F9),
        Face.thinking => const Color(0xFFC4A4FF),
        Face.speaking => _violet,
        Face.idle => const Color(0xFF5B3FA8),
      };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('E.V.A.', style: TextStyle(letterSpacing: 4, fontSize: 16)),
        actions: [
          Row(children: [
            const Text('Wake word', style: TextStyle(fontSize: 12)),
            Switch(
                value: wakeMode,
                onChanged: (v) {
                  setState(() => wakeMode = v);
                  client.wake(v);
                }),
          ]),
          IconButton(icon: const Icon(Icons.settings_outlined), onPressed: _openSettings),
        ],
      ),
      body: SafeArea(
        child: Column(children: [
          Expanded(
            flex: 5,
            child: Center(
              child: GestureDetector(
                onTap: _listen,
                child: AnimatedBuilder(
                  animation: pulse,
                  builder: (_, __) {
                    final active = face == Face.listening || face == Face.speaking;
                    final scale = active ? 1.0 + pulse.value * 0.08 : 1.0;
                    return Transform.scale(
                      scale: scale,
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 250),
                        width: 170,
                        height: 170,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          gradient: RadialGradient(colors: [_faceColor.withValues(alpha: 0.95), _faceColor.withValues(alpha: 0.15)]),
                          boxShadow: [BoxShadow(color: _faceColor.withValues(alpha: 0.45), blurRadius: 60, spreadRadius: 6)],
                        ),
                      ),
                    );
                  },
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Text(caption, textAlign: TextAlign.center, style: const TextStyle(fontSize: 17, height: 1.35)),
          ),
          if (confirmText != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                FilledButton(onPressed: () => _send('yes'), child: const Text('Yes, go ahead')),
                const SizedBox(width: 12),
                OutlinedButton(onPressed: () => _send('no'), child: const Text('No')),
              ]),
            ),
          Expanded(
            flex: 4,
            child: ListView(
              reverse: true,
              padding: const EdgeInsets.all(16),
              children: [
                for (final l in lines.reversed.take(30))
                  Align(
                    alignment: l.eva ? Alignment.centerLeft : Alignment.centerRight,
                    child: Container(
                      margin: const EdgeInsets.symmetric(vertical: 4),
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      constraints: const BoxConstraints(maxWidth: 300),
                      decoration: BoxDecoration(
                        color: l.eva ? const Color(0x228B5CF6) : const Color(0x14FFFFFF),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Text(l.text),
                    ),
                  ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: input,
                  onSubmitted: (_) => _send(),
                  decoration: const InputDecoration(hintText: 'Type to E.V.A.', border: OutlineInputBorder()),
                ),
              ),
              IconButton.filled(icon: const Icon(Icons.mic), onPressed: _listen),
            ]),
          ),
        ]),
      ),
    );
  }
}

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.settings});
  final Settings settings;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final url = TextEditingController(text: widget.settings.url);
  late final token = TextEditingController(text: widget.settings.token);
  final pc = TextEditingController();
  String ca = '';
  String note = '';

  @override
  void initState() {
    super.initState();
    ca = widget.settings.ca;
    if (ca.isNotEmpty) note = 'Pinned certificate: ${caFingerprint(Uint8List.fromList(utf8.encode(ca)))}';
  }

  Future<void> _pin() async {
    try {
      final pem = await fetchCa(pc.text);
      final fp = caFingerprint(pem);
      if (!mounted) return;
      final ok = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
          title: const Text('Trust E.V.A.\'s certificate?'),
          content: Text('Fingerprint:\n$fp\n\nOnly say yes if E.V.A.\'s terminal shows the same start.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('No')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('It matches')),
          ],
        ),
      );
      if (ok == true) {
        setState(() {
          ca = utf8.decode(pem);
          note = 'Pinned certificate: $fp';
          if (url.text.isEmpty) url.text = 'https://${pc.text.trim()}:8443';
        });
      }
    } catch (e) {
      setState(() => note = 'Couldn\'t fetch it: $e');
    }
  }

  Future<void> _assistantRole() async {
    final r = await _assistant.invokeMethod<String>('requestAssistantRole');
    setState(() => note = switch (r) {
          'held' => 'E.V.A. is already your phone\'s assistant.',
          'asked' => 'Confirm E.V.A. in the dialog. Then long-press power (or your assistant gesture).',
          _ => 'Pick E.V.A. under "Digital assistant app" in the settings that just opened.',
        });
  }

  Future<void> _save() async {
    try {
      EvaClient.wsUri(url.text);
    } on FormatException catch (e) {
      setState(() => note = e.message);
      return;
    }
    final s = Settings(url.text.trim(), token.text.trim(), ca);
    await s.save();
    if (mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Connect to E.V.A.')),
        body: ListView(padding: const EdgeInsets.all(20), children: [
          const Text('Anywhere (Tailscale): https://<your-pc>.<tailnet>.ts.net\n'
              'Home Wi-Fi: https://192.168.x.x:8443 (pin the certificate below first)'),
          const SizedBox(height: 12),
          TextField(controller: url, decoration: const InputDecoration(labelText: 'E.V.A. address', border: OutlineInputBorder())),
          const SizedBox(height: 12),
          TextField(
              controller: token,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Remote token (data\\remote_token.txt)', border: OutlineInputBorder())),
          const SizedBox(height: 24),
          const Text('Home Wi-Fi only: E.V.A.\'s certificate'),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(
                child: TextField(controller: pc, decoration: const InputDecoration(labelText: 'PC address, e.g. 192.168.188.101', border: OutlineInputBorder()))),
            const SizedBox(width: 8),
            OutlinedButton(onPressed: _pin, child: const Text('Fetch')),
          ]),
          const SizedBox(height: 24),
          OutlinedButton.icon(
              icon: const Icon(Icons.assistant_outlined),
              onPressed: _assistantRole,
              label: const Text('Make E.V.A. my phone assistant')),
          const SizedBox(height: 16),
          if (note.isNotEmpty) Text(note, style: const TextStyle(color: Color(0xFFC4A4FF))),
          const SizedBox(height: 24),
          FilledButton(onPressed: _save, child: const Text('Save and connect')),
        ]),
      );
}
