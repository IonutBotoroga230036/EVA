package nl.ionut.eva_android

import android.Manifest
import android.app.role.RoleManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.AlarmClock
import android.content.pm.PackageManager
import android.os.Build
import android.net.Uri
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

// Bridges Android's assistant plumbing to the Flutter app:
//   requestMic            Android's microphone permission (the page's mic is granted only after this)
//   launchedByAssist      was this start a long-press power / assistant gesture? (then listen at once)
//   requestAssistantRole  OPTIONAL: make E.V.A. the "Digital assistant app" instead of Gemini
//   assist (to Flutter)   the gesture happened while E.V.A. was already open
class MainActivity : FlutterActivity() {
    private var channel: MethodChannel? = null
    private var launchedByAssist = false
    private var micResult: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        launchedByAssist = isAssist(intent)
        handleShare(intent)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "eva/assistant").also { ch ->
            ch.setMethodCallHandler { call, result ->
                when (call.method) {
                    "launchedByAssist" -> {
                        result.success(launchedByAssist)
                        launchedByAssist = false
                    }
                    "requestMic" -> requestMic(result)
                    "deviceAction" -> result.success(deviceAction(
                        call.argument<String>("action") ?: "", call.argument<Map<String, Any>>("args") ?: emptyMap()))
                    "requestAssistantRole" -> result.success(requestRole())
                    // v0.3 9b: "Eva" with the screen off (WakeService) and the overlay (OverlayActivity)
                    "startHandsFree" -> result.success(startHandsFree(
                        call.argument<String>("address") ?: "", call.argument<String>("token") ?: "",
                        call.argument<String>("audio") ?: "media", call.argument<Boolean>("ask") ?: true))
                    "stopHandsFree" -> {
                        WakeService.stop(this)
                        result.success(true)
                    }
                    "handsFreeStatus" -> result.success(mapOf(
                        "running" to (WakeService.instance != null),
                        "overlayAllowed" to Settings.canDrawOverlays(this)))
                    "isAssistant" -> result.success(isAssistant())
                    // v0.3.2 (9c): WhatsApp/Instagram messages through Notification access (MessageListener)
                    "setLink" -> result.success(EvaLink.set(
                        call.argument<String>("address") ?: "", call.argument<String>("token") ?: ""))
                    "messagesStatus" -> result.success(mapOf(
                        "on" to MessageListener.enabled(this), "access" to MessageListener.accessGranted(this)))
                    "setMessages" -> result.success(setMessages(call.argument<Boolean>("on") ?: false))
                    // Nov 23: location for place reminders (EvaLocation, inside the hands-free service)
                    "locationStatus" -> result.success(mapOf("on" to EvaLocation.isOn(this),
                        "allowed" to EvaLocation.allowed(this)))
                    "setLocation" -> result.success(setLocation(call.argument<Boolean>("on") ?: false))
                    else -> result.notImplemented()
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        WakeService.appVisible = true                      // the page listens while the app is in front
    }

    override fun onPause() {
        WakeService.appVisible = false                     // back to the wake service (if it's on)
        super.onPause()
    }

    /** "started", or what's still missing: "notifications" / "overlay" (the settings screen opens for it). */
    private fun startHandsFree(address: String, token: String, audio: String, ask: Boolean): String {
        if (!address.startsWith("https://") || token.isEmpty()) return "bad_address"
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return "mic"
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            if (ask) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), NOTIFY_REQUEST)
            return "notifications"
        }
        if (!Settings.canDrawOverlays(this)) {
            if (ask) startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
            return "overlay"
        }
        getSharedPreferences("eva_wake", MODE_PRIVATE).edit().putString("audio", audio).apply()
        WakeService.start(this, address, token)
        return "started"
    }

    /** "on" (hands-free restarts with location), "permission" (Android asked), or "off". */
    private fun setLocation(on: Boolean): String {
        EvaLocation.setOn(this, on)
        if (on && !EvaLocation.allowed(this)) {
            requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION), LOCATION_REQUEST)
            return "permission"
        }
        val a = WakeService.address
        val t = WakeService.token
        if (WakeService.instance != null && a != null && t != null) {   // restart so the service gains location
            WakeService.stop(this)
            WakeService.start(this, a, t)
        }
        return if (on) "on" else "off"
    }

    /** "on", "access" (Android's Notification access screen opened: allow E.V.A., come back) or "off". */
    private fun setMessages(on: Boolean): String {
        MessageListener.setEnabled(this, on)
        if (!on) return "off"
        if (!MessageListener.accessGranted(this)) {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            return "access"
        }
        return "on"
    }

    /** Nov 23: Share > E.V.A. from Pixel Recorder (or any app): a recording or a transcript becomes meeting notes. */
    private fun handleShare(i: Intent?) {
        if (i?.action != Intent.ACTION_SEND) return
        val text = i.getStringExtra(Intent.EXTRA_TEXT)
        @Suppress("DEPRECATION") val uri = i.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
        if (uri == null && text.isNullOrBlank()) return
        val ctx = applicationContext
        android.widget.Toast.makeText(ctx, "Sending to E.V.A. for notes...", android.widget.Toast.LENGTH_SHORT).show()
        Thread {
            var waited = 0
            while ((EvaLink.address == null || EvaLink.token == null) && waited < 20_000) {   // the page sets the link
                Thread.sleep(500)
                waited += 500
            }
            val ok = try {
                val name: String
                val bytes: ByteArray
                if (uri != null) {
                    name = contentResolver.query(uri, null, null, null, null)?.use { c ->
                        val col = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                        if (col >= 0 && c.moveToFirst()) c.getString(col) else null
                    } ?: "recording.m4a"
                    bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: ByteArray(0)
                } else {
                    name = "transcript.txt"
                    bytes = text!!.toByteArray(Charsets.UTF_8)
                }
                uploadShared(name, bytes)
            } catch (e: Exception) {
                false
            }
            runOnUiThread {
                android.widget.Toast.makeText(ctx, if (ok) "E.V.A. is making the notes. She'll tell you when they're ready."
                    else "Couldn't reach E.V.A. Is the PC on?", android.widget.Toast.LENGTH_LONG).show()
            }
        }.start()
    }

    private fun uploadShared(name: String, bytes: ByteArray): Boolean {
        val address = EvaLink.address ?: return false
        val c = java.net.URL("$address/api/meetings/upload").openConnection() as java.net.HttpURLConnection
        c.requestMethod = "POST"
        c.doOutput = true
        c.setRequestProperty("Authorization", "Bearer ${EvaLink.token}")
        c.setRequestProperty("Content-Type", "application/octet-stream")
        c.setRequestProperty("X-Filename", name.replace(Regex("[^\\w. -]"), "_"))
        c.setFixedLengthStreamingMode(bytes.size)
        c.connectTimeout = 8000
        c.readTimeout = 120_000
        return try {
            c.outputStream.use { it.write(bytes) }
            c.responseCode == 200
        } finally {
            c.disconnect()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
        if (isAssist(intent)) channel?.invokeMethod("assist", null)
    }

    // v0.3 9c: alarms and timers through Android's own clock app (shared with the overlay: DeviceActions.kt)
    private fun deviceAction(action: String, args: Map<String, Any>): Map<String, Any> = DeviceActions.run(this, action, args)

    private fun requestMic(result: MethodChannel.Result) {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            result.success(true)
            return
        }
        micResult = result
        requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), MIC_REQUEST)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == MIC_REQUEST) {
            micResult?.success(grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED)
            micResult = null
        }
    }

    private fun isAssist(i: Intent?): Boolean =
        i?.action == Intent.ACTION_ASSIST || i?.action == Intent.ACTION_VOICE_COMMAND

    private fun isAssistant(): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
            getSystemService(RoleManager::class.java)?.isRoleHeld(RoleManager.ROLE_ASSISTANT) == true

    private fun requestRole(): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val rm = getSystemService(RoleManager::class.java)
            if (rm != null && rm.isRoleAvailable(RoleManager.ROLE_ASSISTANT)) {
                if (rm.isRoleHeld(RoleManager.ROLE_ASSISTANT)) return "held"
                try {
                    startActivityForResult(rm.createRequestRoleIntent(RoleManager.ROLE_ASSISTANT), 4242)
                    return "asked"
                } catch (e: Exception) {
                    // some phones don't show the dialog for the assistant role: fall through to Settings
                }
            }
        }
        startActivity(Intent(Settings.ACTION_VOICE_INPUT_SETTINGS))
        return "settings"
    }

    companion object {
        private const val MIC_REQUEST = 7001
        private const val NOTIFY_REQUEST = 7002
        private const val LOCATION_REQUEST = 7003
    }
}
