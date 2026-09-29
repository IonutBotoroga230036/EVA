// E.V.A. for Android (v0.3 milestone 9, option A): your real E.V.A. interface in a native shell.
//
// The page is the same one you use on the PC (same orb, animation, panels), loaded from your PC over https.
// Its microphone and her voice run inside the page, on the path that already works on the PC. The shell
// adds what a web page can't: Android's mic permission, audio without a tap, the token kept in encrypted
// storage, trust for your own certificate, and (optionally) the assistant gesture.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';

const _native = MethodChannel('eva/assistant');
const _store = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));
const _bg = Color(0xFF0B0814);
const _violet = Color(0xFF8B5CF6);

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(MaterialApp(
    title: 'E.V.A.',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: _bg,
        colorScheme: ColorScheme.fromSeed(seedColor: _violet, brightness: Brightness.dark),
        useMaterial3: true),
    home: const Launcher(),
  ));
}

/// https only: the token must never travel unencrypted.
Uri? evaAddress(String text) {
  final u = Uri.tryParse(text.trim());
  if (u == null || u.scheme != 'https' || u.host.isEmpty) return null;
  return u.replace(path: '/', query: null, fragment: null);
}

class Launcher extends StatefulWidget {
  const Launcher({super.key});

  @override
  State<Launcher> createState() => _LauncherState();
}

class _LauncherState extends State<Launcher> {
  String? url, token, audio;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final u = await _store.read(key: 'url') ?? '';
    final t = await _store.read(key: 'token') ?? '';
    final a = await _store.read(key: 'audio') ?? 'media';
    setState(() {
      url = u;
      token = t;
      audio = a;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (url == null) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    final address = evaAddress(url!);
    if (address == null || (token ?? '').isEmpty) {
      return SetupScreen(url: url!, token: token ?? '', audio: audio ?? 'media', onSaved: _load);
    }
    return EvaScreen(address: address, token: token!, audio: audio ?? 'media', onSettings: () => setState(() => url = ''));
  }
}

class EvaScreen extends StatefulWidget {
  const EvaScreen({super.key, required this.address, required this.token, required this.audio, required this.onSettings});
  final Uri address;
  final String token;
  final String audio;                                      // 'media' (best sound) or 'call' (talk over her)
  final VoidCallback onSettings;

  @override
  State<EvaScreen> createState() => _EvaScreenState();
}

class _EvaScreenState extends State<EvaScreen> {
  WebViewController? controller;
  String? error;
  bool micAllowed = false, listenWhenLoaded = false;

  @override
  void initState() {
    super.initState();
    _native.setMethodCallHandler((call) async {
      if (call.method == 'assist') _tapMic();                 // the assistant gesture while E.V.A. is open
    });
    _open();
  }

  Future<void> _open() async {
    micAllowed = await _native.invokeMethod<bool>('requestMic') ?? false;
    listenWhenLoaded = await _native.invokeMethod<bool>('launchedByAssist') ?? false;

    // The token as E.V.A.'s pairing cookie, set natively: it never appears in a URL or her log.
    await WebViewCookieManager().setCookie(WebViewCookie(
        name: 'eva_token', value: widget.token, domain: widget.address.host, path: '/'));

    final c = WebViewController(onPermissionRequest: (WebViewPermissionRequest request) {
      final onlyMic = request.types.isNotEmpty &&
          request.types.every((t) => t == WebViewPermissionResourceType.microphone);
      if (micAllowed && onlyMic) {
        request.grant();                                       // the microphone, nothing else
      } else {
        request.deny();
      }
    });
    await c.setJavaScriptMode(JavaScriptMode.unrestricted);
    await c.setBackgroundColor(_bg);
    final ua = await c.getUserAgent() ?? '';
    await c.setUserAgent('$ua EVA-Android/0.3${widget.audio == 'call' ? ' audio=call' : ''}');
    // The page's hands on this phone (v0.3 9c): alarms and timers through Android's own clock app.
    await c.addJavaScriptChannel('EvaNative', onMessageReceived: (JavaScriptMessage m) async {
      Map<String, dynamic> req;
      try {
        req = jsonDecode(m.message) as Map<String, dynamic>;
      } catch (_) {
        return;
      }
      Map<String, dynamic> res;
      try {
        final r = await _native.invokeMethod<Map>('deviceAction', {'action': req['action'], 'args': req['args'] ?? {}});
        res = Map<String, dynamic>.from(r ?? {'ok': false, 'error': 'no answer'});
      } catch (e) {
        res = {'ok': false, 'error': '$e'};
      }
      res['id'] = req['id'];
      await c.runJavaScript("window.dispatchEvent(new CustomEvent('eva:device-result', {detail: ${jsonEncode(res)}}))");
    });
    await c.setNavigationDelegate(NavigationDelegate(
      onNavigationRequest: (r) {
        final host = Uri.tryParse(r.url)?.host ?? '';
        return host == widget.address.host ? NavigationDecision.navigate : NavigationDecision.prevent;
      },
      onPageFinished: (_) {
        if (listenWhenLoaded) {
          listenWhenLoaded = false;
          Future.delayed(const Duration(milliseconds: 1200), _tapMic);
        }
      },
      onWebResourceError: (e) {
        if (e.isForMainFrame ?? true) setState(() => error = e.description);
      },
    ));
    if (c.platform is AndroidWebViewController) {
      await (c.platform as AndroidWebViewController).setMediaPlaybackRequiresUserGesture(false);  // her voice
    }
    await c.loadRequest(widget.address, headers: {'Authorization': 'Bearer ${widget.token}'});
    setState(() {
      controller = c;
      error = null;
    });
  }

  void _tapMic() => controller?.runJavaScript("window.dispatchEvent(new CustomEvent('eva:mic'))");

  Future<void> _menu() async {
    final choice = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: const Color(0xFF16102A),
      builder: (_) => SafeArea(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
        ListTile(leading: const Icon(Icons.refresh), title: const Text('Reload'), onTap: () => Navigator.pop(context, 'reload')),
        ListTile(leading: const Icon(Icons.settings_outlined), title: const Text('Connection settings'), onTap: () => Navigator.pop(context, 'settings')),
        ListTile(leading: const Icon(Icons.close), title: const Text('Leave E.V.A.'), onTap: () => Navigator.pop(context, 'leave')),
      ])),
    );
    if (choice == 'reload') {
      setState(() => error = null);
      await controller?.reload();
    } else if (choice == 'settings') {
      widget.onSettings();
    } else if (choice == 'leave') {
      await SystemNavigator.pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = controller;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        if (c != null && await c.canGoBack()) {
          await c.goBack();
        } else {
          await _menu();
        }
      },
      child: Scaffold(
        backgroundColor: _bg,
        body: SafeArea(
          child: Stack(children: [
            if (c != null) WebViewWidget(controller: c),
            if (c == null && error == null) const Center(child: CircularProgressIndicator()),
            if (!micAllowed && c != null)
              Positioned(
                  left: 12, right: 12, bottom: 12,
                  child: Material(
                      color: const Color(0xCC16102A),
                      borderRadius: BorderRadius.circular(12),
                      child: const Padding(
                          padding: EdgeInsets.all(12),
                          child: Text('Microphone permission is off for E.V.A., so she can\'t hear you. '
                              'Allow it in Android settings > Apps > E.V.A. > Permissions.')))),
            if (error != null)
              Center(
                  child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Column(mainAxisSize: MainAxisSize.min, children: [
                        Text('Can\'t reach E.V.A.\n$error', textAlign: TextAlign.center),
                        const SizedBox(height: 16),
                        FilledButton(onPressed: _open, child: const Text('Try again')),
                        TextButton(onPressed: widget.onSettings, child: const Text('Connection settings')),
                      ]))),
          ]),
        ),
      ),
    );
  }
}

class SetupScreen extends StatefulWidget {
  const SetupScreen({super.key, required this.url, required this.token, required this.audio, required this.onSaved});
  final String url, token, audio;
  final VoidCallback onSaved;

  @override
  State<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends State<SetupScreen> {
  late final url = TextEditingController(text: widget.url);
  late final token = TextEditingController(text: widget.token);
  late String audio = widget.audio;
  String note = '';

  Future<void> _save() async {
    if (evaAddress(url.text) == null) {
      setState(() => note = 'Use an https:// address: Tailscale (https://<pc>.<tailnet>.ts.net) or https://192.168.x.x:8443.');
      return;
    }
    if (token.text.trim().isEmpty) {
      setState(() => note = 'The token is in data\\remote_token.txt on the PC.');
      return;
    }
    await _store.write(key: 'url', value: url.text.trim());
    await _store.write(key: 'token', value: token.text.trim());
    await _store.write(key: 'audio', value: audio);
    widget.onSaved();
  }

  Future<void> _assistant() async {
    final r = await _native.invokeMethod<String>('requestAssistantRole');
    setState(() => note = switch (r) {
          'held' => 'E.V.A. is your long-press assistant. Switch back any time: Settings > Apps > Default apps > Digital assistant app.',
          'asked' => 'Confirm in the dialog. To go back to Gemini later: Settings > Apps > Default apps > Digital assistant app.',
          _ => 'Pick E.V.A. (or Gemini, to go back) under "Digital assistant app".',
        });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Connect to E.V.A.')),
        body: ListView(padding: const EdgeInsets.all(20), children: [
          const Text('Anywhere (Tailscale, recommended): https://<your-pc>.<tailnet>.ts.net\n'
              'Home Wi-Fi: https://192.168.x.x:8443 (install E.V.A.\'s certificate in Android first, '
              'see docs/PHONE_SETUP.md)'),
          const SizedBox(height: 16),
          TextField(controller: url, keyboardType: TextInputType.url,
              decoration: const InputDecoration(labelText: 'E.V.A. address', border: OutlineInputBorder())),
          const SizedBox(height: 12),
          TextField(controller: token, obscureText: true,
              decoration: const InputDecoration(labelText: 'Remote token', border: OutlineInputBorder())),
          const SizedBox(height: 20),
          const Text('Her voice on this phone'),
          const SizedBox(height: 8),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'media', label: Text('Media (best sound)')),
              ButtonSegment(value: 'call', label: Text('Call (talk over her)')),
            ],
            selected: {audio},
            onSelectionChanged: (s) => setState(() => audio = s.first),
          ),
          const SizedBox(height: 4),
          const Text('Call mode lets you interrupt her by talking, but Android plays her like a phone call.',
              style: TextStyle(fontSize: 12)),
          const SizedBox(height: 20),
          FilledButton(onPressed: _save, child: const Text('Save and connect')),
          const SizedBox(height: 32),
          const Text('Optional: long-press power opens E.V.A. instead of Gemini. Easy to switch back.'),
          const SizedBox(height: 8),
          OutlinedButton.icon(icon: const Icon(Icons.assistant_outlined), onPressed: _assistant,
              label: const Text('Use E.V.A. for long-press')),
          if (note.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 16),
              child: Text(note, style: const TextStyle(color: Color(0xFFC4A4FF)))),
        ]),
      );
}
