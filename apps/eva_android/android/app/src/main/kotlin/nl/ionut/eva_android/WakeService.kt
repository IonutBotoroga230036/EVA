package nl.ionut.eva_android

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.max
import kotlin.math.sqrt

/**
 * v0.3 9b: "Eva" with the screen off, on the phone itself.
 *
 * A foreground service (the notification says it's listening, with a Stop button) reads the mic at 16 kHz and
 * runs your wake word model on-device (WakeEngine). Nothing leaves the phone until it hears "Eva". Then it:
 *   1. opens the overlay (OverlayActivity) over whatever is on screen, or the lock screen,
 *   2. keeps recording until you stop talking, so "Eva, what time is it" works in one breath,
 *   3. gives the mic back and hands that recording to the overlay, which sends it to your PC.
 * While the E.V.A. app itself is open, the page listens as before and this service pauses.
 *
 * The address and token live only in memory (passed in by the app): after a reboot or if Android ends the
 * service, open E.V.A. once and it starts again. The token is never written to disk by this service.
 */
class WakeService : Service() {
    @Volatile private var running = false
    private var worker: Thread? = null
    private var recorder: AudioRecord? = null
    private var engine: WakeEngine? = null
    private var threshold = 0.5f

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            enabled = false
            stopSelf()
            return START_NOT_STICKY
        }
        intent?.getStringExtra(EXTRA_ADDRESS)?.let { address = it.trimEnd('/') }
        intent?.getStringExtra(EXTRA_TOKEN)?.let { token = it }
        if (address == null || token == null ||
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            stopSelf()
            return START_NOT_STICKY
        }
        val note = notification("Listening for \"Eva\"")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startForeground(NOTE_ID, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTE_ID, note)
        }
        instance = this
        if (!running) {
            running = true
            worker = Thread({ loop() }, "eva-wake").also { it.start() }
        }
        return START_NOT_STICKY            // a microphone service can't restart itself from the background anyway
    }

    override fun onDestroy() {
        running = false
        worker?.interrupt()
        releaseRecorder()
        engine?.close()
        engine = null
        if (instance === this) instance = null
        super.onDestroy()
    }

    // ------------------------------------------------------------------ the listening loop
    private fun loop() {
        val ring = ShortArray(WakeEngine.CLIP)
        var filled = 0
        var chunks = 0
        val chunk = ShortArray(CHUNK)
        var floor = 300.0                                    // background loudness, learned as it goes
        var cooldownUntil = 0L
        while (running) {
            try {
                if (engine == null && !loadModel()) {
                    Thread.sleep(30_000)
                    continue
                }
                if (appVisible || overlayOpen) {             // the app's page (or the overlay) has the mic
                    releaseRecorder()
                    filled = 0
                    Thread.sleep(300)
                    continue
                }
                val rec = recorder ?: openRecorder()
                if (rec == null) {                           // another app holds the mic (a call): try again soon
                    Thread.sleep(2000)
                    continue
                }
                val n = rec.read(chunk, 0, CHUNK)
                if (n <= 0) continue
                System.arraycopy(ring, n, ring, 0, ring.size - n)          // slide: newest audio at the end
                System.arraycopy(chunk, 0, ring, ring.size - n, n)
                filled = minOf(ring.size, filled + n)
                val loud = rms(chunk, n)
                floor = if (loud < floor) floor * 0.9 + loud * 0.1 else floor * 0.995 + loud * 0.005
                chunks++
                if (filled < ring.size / 2 || chunks % 2 != 0 || System.currentTimeMillis() < cooldownUntil) continue
                if (rms(ring, ring.size, ring.size - WakeEngine.SAMPLE_RATE) < max(floor * 2.5, 250.0)) continue  // quiet: save battery
                val s = engine!!.score(ring)
                if (s >= threshold) {
                    Log.i(TAG, "wake word, score %.2f".format(s))
                    onWake(ring, floor)
                    filled = 0
                    cooldownUntil = System.currentTimeMillis() + 1500
                }
            } catch (e: InterruptedException) {
                break
            } catch (e: Exception) {
                Log.w(TAG, "wake loop: ${e.message}")
                releaseRecorder()
                try { Thread.sleep(1000) } catch (_: InterruptedException) { break }
            }
        }
        releaseRecorder()
    }

    /** "Eva" heard: show the overlay at once, record what follows until you pause, then give the mic back. */
    private fun onWake(ring: ShortArray, floor: Double) {
        pcm = null
        overlayOpen = true
        if (!openOverlay()) {
            overlayOpen = false                                    // asked with a notification instead
            return
        }
        val out = ByteArrayOutputStream()
        val keep = (WakeEngine.SAMPLE_RATE * 0.4).toInt()   // the tail of "Eva": the PC strips it from the words
        for (i in ring.size - keep until ring.size) out.write2(ring[i])
        val rec = recorder
        val chunk = ShortArray(CHUNK)
        val start = System.currentTimeMillis()
        var spoke = false
        var lastLoud = start
        val speech = max(floor * 3.0, 500.0)
        while (rec != null && running) {
            val n = rec.read(chunk, 0, CHUNK)
            if (n <= 0) break
            for (i in 0 until n) out.write2(chunk[i])
            val now = System.currentTimeMillis()
            if (rms(chunk, n) > speech) {
                spoke = true
                lastLoud = now
            }
            if (!spoke && now - start > 1600) break                // just "Eva": the overlay will listen
            if (spoke && now - lastLoud > 800) break               // you stopped talking
            if (now - start > 9000) break
        }
        releaseRecorder()                                          // the overlay's page needs the mic now
        pcm = if (spoke) out.toByteArray() else ByteArray(0)
        Log.i(TAG, "after the wake word: ${pcm!!.size / 32} ms of audio")
    }

    private fun openOverlay(): Boolean {
        val i = Intent(this, OverlayActivity::class.java).addFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isInteractive) {                                   // screen off: light it for the overlay
            @Suppress("DEPRECATION")
            pm.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP, "eva:wake")
                .acquire(3000)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
            // Without "Display over other apps", Android won't let a service open a screen: ask with a notification
            val pi = PendingIntent.getActivity(this, 1, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val nm = getSystemService(NotificationManager::class.java)
            nm.notify(NOTE_ID + 1, builder().setContentTitle("E.V.A.").setContentText("Tap to talk (allow \"Display over other apps\" so she opens by herself)")
                .setContentIntent(pi).setAutoCancel(true).build())
            return false
        }
        startActivity(i)
        return true
    }

    // ------------------------------------------------------------------ the model, from your PC
    private fun loadModel(): Boolean {
        val dir = File(filesDir, "wakeword").apply { mkdirs() }
        return try {
            val info = JSONObject(get("/api/wakeword/info").toString(Charsets.UTF_8))
            val version = info.getLong("version")
            threshold = info.getDouble("phone_threshold").toFloat()
            val prefs = getSharedPreferences("eva_wake", MODE_PRIVATE)
            val have = FILES.all { File(dir, it).exists() }
            if (!have || prefs.getLong("version", -1) != version) {
                for (f in FILES) {
                    val tmp = File(dir, "$f.part")
                    tmp.writeBytes(get("/api/wakeword/files/$f"))
                    tmp.renameTo(File(dir, f))
                }
                prefs.edit().putLong("version", version).putFloat("threshold", threshold).apply()
            }
            engine = WakeEngine(dir)
            update("Listening for \"Eva\"")
            true
        } catch (e: Exception) {
            val cached = FILES.all { File(dir, it).exists() }
            Log.w(TAG, "model download failed (${e.message}); cached copy: $cached")
            if (cached) {                                          // offline PC: the last model still works
                threshold = getSharedPreferences("eva_wake", MODE_PRIVATE).getFloat("threshold", 0.5f)
                engine = WakeEngine(dir)
                update("Listening for \"Eva\"")
                true
            } else {
                update("Waiting for your PC to get the wake word model")
                false
            }
        }
    }

    private fun get(path: String): ByteArray {
        val c = URL(address + path).openConnection() as HttpURLConnection
        c.setRequestProperty("Authorization", "Bearer $token")    // https only: the app refuses http addresses
        c.connectTimeout = 8000
        c.readTimeout = 20000
        try {
            if (c.responseCode != 200) throw IllegalStateException("HTTP ${c.responseCode} for $path")
            return c.inputStream.use { it.readBytes() }
        } finally {
            c.disconnect()
        }
    }

    // ------------------------------------------------------------------ audio
    private fun openRecorder(): AudioRecord? {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return null
        val min = AudioRecord.getMinBufferSize(WakeEngine.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val r = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, WakeEngine.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(min, CHUNK * 8))
        if (r.state != AudioRecord.STATE_INITIALIZED) {
            r.release()
            return null
        }
        r.startRecording()
        recorder = r
        return r
    }

    private fun releaseRecorder() {
        recorder?.let {
            try { it.stop() } catch (_: Exception) {}
            it.release()
        }
        recorder = null
    }

    private fun rms(a: ShortArray, n: Int, from: Int = 0): Double {
        var s = 0.0
        for (i in from until n) s += a[i].toDouble() * a[i]
        return sqrt(s / max(1, n - from))
    }

    private fun ByteArrayOutputStream.write2(v: Short) {
        write(v.toInt() and 0xff)
        write((v.toInt() shr 8) and 0xff)
    }

    // ------------------------------------------------------------------ notification
    private fun builder(): Notification.Builder {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL) == null) {
                nm.createNotificationChannel(NotificationChannel(CHANNEL, "Wake word", NotificationManager.IMPORTANCE_LOW))
            }
            return Notification.Builder(this, CHANNEL)
        }
        @Suppress("DEPRECATION")
        return Notification.Builder(this)
    }

    private fun notification(text: String): Notification {
        val stop = PendingIntent.getService(this, 2, Intent(this, WakeService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE)
        val open = PendingIntent.getActivity(this, 3, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        @Suppress("DEPRECATION")
        return builder().setContentTitle("E.V.A.").setContentText(text)
            .setSmallIcon(applicationInfo.icon).setOngoing(true).setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .build()
    }

    private fun update(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTE_ID, notification(text))
    }

    companion object {
        private const val TAG = "EVA-wake"
        private const val CHANNEL = "eva_wake"
        private const val NOTE_ID = 4207
        private const val CHUNK = 1280                         // 80 ms at 16 kHz
        private const val ACTION_STOP = "nl.ionut.eva_android.STOP_WAKE"
        private const val EXTRA_ADDRESS = "address"
        private const val EXTRA_TOKEN = "token"
        private val FILES = listOf("melspectrogram.onnx", "embedding_model.onnx", "eva.onnx")

        @Volatile var appVisible = false                       // MainActivity in front: the page listens instead
        @Volatile var overlayOpen = false
        @Volatile var enabled = false
        @Volatile var pcm: ByteArray? = null                   // what you said after "Eva" (null: still recording)
        @Volatile var address: String? = null                  // memory only
        @Volatile var token: String? = null                    // memory only, never written to disk here
        @Volatile var instance: WakeService? = null

        fun start(ctx: Context, address: String, token: String) {
            enabled = true
            val i = Intent(ctx, WakeService::class.java).putExtra(EXTRA_ADDRESS, address).putExtra(EXTRA_TOKEN, token)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i) else ctx.startService(i)
        }

        fun stop(ctx: Context) {
            enabled = false
            ctx.stopService(Intent(ctx, WakeService::class.java))
        }

        fun overlayClosed() {
            overlayOpen = false
            pcm = null
        }
    }
}
