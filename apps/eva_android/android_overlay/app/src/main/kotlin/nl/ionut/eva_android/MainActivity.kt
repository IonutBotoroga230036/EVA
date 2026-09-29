package nl.ionut.eva_android

import android.Manifest
import android.app.role.RoleManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.AlarmClock
import android.content.pm.PackageManager
import android.os.Build
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
                    "isAssistant" -> result.success(isAssistant())
                    else -> result.notImplemented()
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (isAssist(intent)) channel?.invokeMethod("assist", null)
    }

    // v0.3 9c: alarms and timers through Android's own clock app (no special permission beyond SET_ALARM).
    private fun deviceAction(action: String, args: Map<String, Any>): Map<String, Any> = try {
        when (action) {
            "set_alarm" -> {
                startActivity(Intent(AlarmClock.ACTION_SET_ALARM).apply {
                    putExtra(AlarmClock.EXTRA_HOUR, (args["hour"] as Number).toInt())
                    putExtra(AlarmClock.EXTRA_MINUTES, (args["minute"] as Number).toInt())
                    putExtra(AlarmClock.EXTRA_MESSAGE, args["label"]?.toString() ?: "E.V.A.")
                    putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                })
                mapOf("ok" to true)
            }
            "set_timer" -> {
                startActivity(Intent(AlarmClock.ACTION_SET_TIMER).apply {
                    putExtra(AlarmClock.EXTRA_LENGTH, (args["seconds"] as Number).toInt())
                    putExtra(AlarmClock.EXTRA_MESSAGE, args["label"]?.toString() ?: "E.V.A.")
                    putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                })
                mapOf("ok" to true)
            }
            else -> mapOf("ok" to false, "error" to "unknown action $action")
        }
    } catch (e: ActivityNotFoundException) {
        mapOf("ok" to false, "error" to "no clock app on this phone handles it")
    } catch (e: Exception) {
        mapOf("ok" to false, "error" to (e.message ?: "it failed"))
    }

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
    }
}
