package nl.ionut.eva_android

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.provider.AlarmClock

/** Alarms and timers through Android's own clock app, the phone's volume, and message replies (MessageListener): used by the app,
 *  the overlay and the screen-off mode. */
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
            "reply_message" -> MessageListener.reply(ctx, args)        // v0.3.2: after your spoken yes
            "set_volume" -> {                                          // the phone's own media volume, 0-100
                val am = ctx.getSystemService(Context.AUDIO_SERVICE) as android.media.AudioManager
                val max = am.getStreamMaxVolume(android.media.AudioManager.STREAM_MUSIC)
                val level = ((args["level"] as? Number)?.toInt() ?: 50).coerceIn(0, 100)
                am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, Math.round(level * max / 100f), 0)
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
