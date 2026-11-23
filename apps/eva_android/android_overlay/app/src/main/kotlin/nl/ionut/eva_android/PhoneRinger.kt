package nl.ionut.eva_android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log

/** Nov 23, "find my phone": the alarm sound at full alarm volume (alarms play on silent too), looping until
 *  "found it", the notification's button, or the time runs out. The alarm volume goes back to where it was. */
object PhoneRinger {
    private const val TAG = "EVA-ring"
    private const val CHANNEL = "eva_ring"
    private const val NOTE_ID = 4211
    private const val ACTION_STOP = "nl.ionut.eva_android.STOP_RING"
    private val handler = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var savedVolume = -1
    private var receiver: BroadcastReceiver? = null

    @Synchronized
    fun start(ctx: Context, seconds: Int): Map<String, Any> {
        stop(ctx)
        val app = ctx.applicationContext
        val am = app.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        try {
            savedVolume = am.getStreamVolume(AudioManager.STREAM_ALARM)
            am.setStreamVolume(AudioManager.STREAM_ALARM, am.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0)
        } catch (e: SecurityException) {                       // Do Not Disturb may refuse; it still rings
            Log.w(TAG, "alarm volume unchanged: ${e.message}")
            savedVolume = -1
        }
        val uri = RingtoneManager.getActualDefaultRingtoneUri(app, RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            ?: return mapOf("ok" to false, "error" to "there's no alarm sound on this phone")
        return try {
            val mp = MediaPlayer()
            mp.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
            mp.setDataSource(app, uri)
            mp.isLooping = true
            mp.prepare()
            mp.start()
            player = mp
            showNotification(app)
            handler.postDelayed({ stop(app) }, seconds.coerceIn(5, 120) * 1000L)
            mapOf("ok" to true)
        } catch (e: Exception) {
            stop(app)
            mapOf("ok" to false, "error" to (e.message ?: "it couldn't play a sound"))
        }
    }

    @Synchronized
    fun stop(ctx: Context): Map<String, Any> {
        val app = ctx.applicationContext
        handler.removeCallbacksAndMessages(null)
        player?.let {
            try { it.stop() } catch (_: Exception) {}
            it.release()
        }
        player = null
        if (savedVolume >= 0) {
            try {
                (app.getSystemService(Context.AUDIO_SERVICE) as AudioManager)
                    .setStreamVolume(AudioManager.STREAM_ALARM, savedVolume, 0)
            } catch (_: SecurityException) {}
            savedVolume = -1
        }
        receiver?.let { try { app.unregisterReceiver(it) } catch (_: Exception) {} }
        receiver = null
        app.getSystemService(NotificationManager::class.java).cancel(NOTE_ID)
        return mapOf("ok" to true)
    }

    private fun showNotification(app: Context) {
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) { stop(c) }
        }
        if (Build.VERSION.SDK_INT >= 33) app.registerReceiver(r, IntentFilter(ACTION_STOP), Context.RECEIVER_NOT_EXPORTED)
        else app.registerReceiver(r, IntentFilter(ACTION_STOP))
        receiver = r
        val nm = app.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Find my phone", NotificationManager.IMPORTANCE_HIGH))
        }
        val stopPi = PendingIntent.getBroadcast(app, 7, Intent(ACTION_STOP).setPackage(app.packageName),
            PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(app, CHANNEL).setContentTitle("E.V.A.: here I am")
            .setContentText("Tap to stop ringing").setSmallIcon(app.applicationInfo.icon)
            .setContentIntent(stopPi).setDeleteIntent(stopPi).setAutoCancel(true)
            .addAction(Notification.Action.Builder(null, "Found it", stopPi).build())
            .build()
        try { nm.notify(NOTE_ID, n) } catch (_: SecurityException) {}
    }
}
