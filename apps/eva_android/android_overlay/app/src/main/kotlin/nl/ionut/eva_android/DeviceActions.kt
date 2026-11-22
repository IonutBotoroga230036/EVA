package nl.ionut.eva_android

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.provider.AlarmClock

/** Alarms and timers through Android's own clock app: used by the app, the overlay and the screen-off mode. */
object DeviceActions {
    fun run(ctx: Context, action: String, args: Map<String, Any?>): Map<String, Any> = try {
        when (action) {
            "set_alarm" -> {
                ctx.startActivity(Intent(AlarmClock.ACTION_SET_ALARM).apply {
                    putExtra(AlarmClock.EXTRA_HOUR, (args["hour"] as Number).toInt())
                    putExtra(AlarmClock.EXTRA_MINUTES, (args["minute"] as Number).toInt())
                    putExtra(AlarmClock.EXTRA_MESSAGE, args["label"]?.toString() ?: "E.V.A.")
                    putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                })
                mapOf("ok" to true)
            }
            "set_timer" -> {
                ctx.startActivity(Intent(AlarmClock.ACTION_SET_TIMER).apply {
                    putExtra(AlarmClock.EXTRA_LENGTH, (args["seconds"] as Number).toInt())
                    putExtra(AlarmClock.EXTRA_MESSAGE, args["label"]?.toString() ?: "E.V.A.")
                    putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
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

    /** JSON args from the PC ({"hour": 7, "minute": 0}) as a plain map. */
    fun args(json: org.json.JSONObject?): Map<String, Any?> {
        if (json == null) return emptyMap()
        val out = HashMap<String, Any?>()
        val keys = json.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            out[k] = json.opt(k)
        }
        return out
    }
}
