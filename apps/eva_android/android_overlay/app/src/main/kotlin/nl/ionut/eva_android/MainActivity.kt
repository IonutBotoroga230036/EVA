package nl.ionut.eva_android

import android.app.role.RoleManager
import android.content.Intent
import android.os.Build
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

// Bridges Android's assistant plumbing to the Flutter app:
//   launchedByAssist      was this start a long-press power / assistant gesture? (then listen at once)
//   requestAssistantRole  ask Android to make E.V.A. the "Digital assistant app" (ROLE_ASSISTANT)
//   assist (to Flutter)   the gesture happened while E.V.A. was already open
class MainActivity : FlutterActivity() {
    private var channel: MethodChannel? = null
    private var launchedByAssist = false

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
}
