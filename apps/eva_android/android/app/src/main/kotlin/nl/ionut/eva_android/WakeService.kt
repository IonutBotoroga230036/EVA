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
 * runs your wake word model on-device (WakeEngine). Nothing leaves the phone until it thinks it heard "Eva".
 * Then it:
 *   1. keeps recording until you stop talking, so "Eva, what time is it" works in one breath,
 *   2. asks your PC to double-check (Whisper must hear "Eva" in it: Nov 22, dictating near the phone woke her),
 *   3. only then lights the screen and opens the overlay (OverlayActivity) with your words already transcribed.
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
    private var stayDark = false                               // from your PC with every wake ("Eva, stay dark")
    private var lastFloor = 300.0
    private var peak = 0f
    private var peakAt = 0L

    // The PC double-checks every wake, so the phone can afford to be a bit more eager than the trained
    // threshold: fewer missed "Eva"s, and false ones never reach the screen.
    private fun trigger() = threshold * 0.85f

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
            val located = EvaLocation.wanted(this)            // Nov 23: location rides on this service
            try {
                startForeground(NOTE_ID, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                    (if (located) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0))
            } catch (e: SecurityException) {                    // location not allowed right now: the mic still works
                startForeground(NOTE_ID, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            }
        } else {
            startForeground(NOTE_ID, note)
        }
        instance = this
        EvaLocation.start(this)
        try {
            android.webkit.WebView(applicationContext).destroy()   // loads the web engine now, not after "Eva"
        } catch (_: Exception) {}
        if (!running) {
            running = true
            worker = Thread({ loop() }, "eva-wake").also { it.start() }
        }
        return START_NOT_STICKY            // a microphone service can't restart itself from the background anyway
    }

    /** Something the PC wants said (a location reminder), through the same speaker path as her answers. */
    fun speak(b64: String) = play(b64)

    override fun onDestroy() {
        EvaLocation.stop(this)
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
                lastFloor = floor
                chunks++
                if (filled < ring.size / 2 || chunks % 2 != 0 || System.currentTimeMillis() < cooldownUntil) continue
                if (rms(ring, ring.size, ring.size - WakeEngine.SAMPLE_RATE) < max(floor * 2.5, 250.0)) continue  // quiet: save battery
                val s = engine!!.score(ring)
                if (s > peak) peak = s
                if (System.currentTimeMillis() - peakAt > 5000) {      // adb logcat -s EVA-wake shows these
                    if (peak > 0.05f) Log.d(TAG, "loudest moment, last 5 s: score %.2f (fires at %.2f)".format(peak, trigger()))
                    peak = 0f
                    peakAt = System.currentTimeMillis()
                }
                if (s >= trigger()) {
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
        command = null
        val out = ByteArrayOutputStream()
        val keep = (WakeEngine.SAMPLE_RATE * 1.2).toInt()   // "Eva" itself, so the PC can check it was said
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
            if (!spoke && now - start > 1000) break                // just "Eva": the overlay will listen
            if (spoke && now - lastLoud > 1300) break              // you stopped talking (800 ms cut "can you...")
            if (now - start > 9000) break
        }
        val audio = out.toByteArray()
        val verdict = verify(audio)                                // true / false / null (PC unreachable)
        if (verdict == false) {
            Log.i(TAG, "not for her (the PC heard no \"Eva\"), listening again")
            return                                                 // recorder still open: straight back to listening
        }
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (verdict == true && stayDark && !pm.isInteractive) {    // "Eva, stay dark": voice only, screen stays off
            darkConversation(command ?: "")
            return
        }
        releaseRecorder()                                          // the overlay's page needs the mic now
        pcm = if (verdict == null && spoke) audio else ByteArray(0) // offline check: the overlay sends the audio
        overlayOpen = true
        if (!openOverlay()) overlayOpen = false                    // asked with a notification instead
    }

    /** The PC's double-check. true: it was "Eva" (command set), false: it wasn't, null: couldn't ask. */
    private fun verify(audio: ByteArray): Boolean? = try {
        val c = URL("$address/api/wakeword/verify").openConnection() as HttpURLConnection
        c.requestMethod = "POST"
        c.doOutput = true
        c.setRequestProperty("Authorization", "Bearer $token")
        c.setRequestProperty("Content-Type", "application/octet-stream")
        c.connectTimeout = 3000
        c.readTimeout = 6000
        try {
            c.outputStream.use { it.write(audio) }
            if (c.responseCode != 200) null else {
                val j = JSONObject(c.inputStream.use { it.readBytes() }.toString(Charsets.UTF_8))
                if (j.optBoolean("wake")) command = j.optString("command", "")
                stayDark = j.optBoolean("stay_dark", false)
                j.optBoolean("wake")
            }
        } finally {
            c.disconnect()
        }
    } catch (e: Exception) {
        Log.w(TAG, "couldn't ask the PC to check (${e.message}); opening anyway")
        null
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

    // ------------------------------------------------------------------ "stay dark": voice only, screen off
    /** The whole conversation from here: your words go to the PC, her answer comes back as audio and plays, then
     *  she listens for a follow-up (no "Eva" needed) until you stop talking or say "that's all". */
    private fun darkConversation(first: String) {
        releaseRecorder()
        var body = JSONObject().put("text", first).put("locked", locked())
        for (turn in 0 until 8) {
            val res = post("/api/phone/turn", body) ?: break
            val actions = res.optJSONArray("actions")
            if (actions != null) for (i in 0 until actions.length()) {
                val a = actions.getJSONObject(i)
                DeviceActions.run(this, a.optString("action"), DeviceActions.args(a.optJSONObject("args")))
            }
            play(res.optString("audio"))
            if (!res.optBoolean("listen")) break
            val next = recordFollowUp() ?: break                   // quiet: the conversation is over
            body = JSONObject().put("pcm", android.util.Base64.encodeToString(next, android.util.Base64.NO_WRAP))
                .put("locked", locked())
        }
        Log.i(TAG, "screen-off conversation ended, listening for \"Eva\" again")
    }

    private fun locked(): Boolean =
        (getSystemService(Context.KEYGUARD_SERVICE) as android.app.KeyguardManager).isKeyguardLocked

    private fun post(path: String, json: JSONObject): JSONObject? = try {
        val c = URL(address + path).openConnection() as HttpURLConnection
        c.requestMethod = "POST"
        c.doOutput = true
        c.setRequestProperty("Authorization", "Bearer $token")
        c.setRequestProperty("Content-Type", "application/json")
        c.connectTimeout = 4000
        c.readTimeout = 90_000                                     // a turn can take a while (tools, Claude)
        try {
            c.outputStream.use { it.write(json.toString().toByteArray(Charsets.UTF_8)) }
            if (c.responseCode != 200) null
            else JSONObject(c.inputStream.use { it.readBytes() }.toString(Charsets.UTF_8))
        } finally {
            c.disconnect()
        }
    } catch (e: Exception) {
        Log.w(TAG, "screen-off turn failed: ${e.message}")
        null
    }

    /** Her answer (WAV, base64) through the speaker or your headphones, ducking any music meanwhile. */
    private fun play(b64: String) {
        if (b64.isEmpty()) return
        val f = File(cacheDir, "eva_reply.wav")
        f.writeBytes(android.util.Base64.decode(b64, android.util.Base64.DEFAULT))
        val am = getSystemService(Context.AUDIO_SERVICE) as android.media.AudioManager
        val attrs = android.media.AudioAttributes.Builder()
            .setUsage(android.media.AudioAttributes.USAGE_ASSISTANT)
            .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SPEECH).build()
        val focus = android.media.AudioFocusRequest.Builder(android.media.AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(attrs).build()
        am.requestAudioFocus(focus)
        val done = java.util.concurrent.CountDownLatch(1)
        val mp = android.media.MediaPlayer()
        try {
            mp.setAudioAttributes(attrs)
            mp.setDataSource(f.absolutePath)
            mp.setOnCompletionListener { done.countDown() }
            mp.setOnErrorListener { _, _, _ -> done.countDown(); true }
            mp.prepare()
            mp.start()
            done.await(mp.duration.toLong() + 3000, java.util.concurrent.TimeUnit.MILLISECONDS)
        } catch (e: Exception) {
            Log.w(TAG, "couldn't play her answer: ${e.message}")
        } finally {
            mp.release()
            am.abandonAudioFocusRequest(focus)
        }
    }

    /** A follow-up after her answer: up to 6 s to start, then until you pause. null when you said nothing. */
    private fun recordFollowUp(): ByteArray? {
        val rec = openRecorder() ?: return null
        val chunk = ShortArray(CHUNK)
        val pre = java.util.ArrayDeque<ShortArray>()
        val out = ByteArrayOutputStream()
        val speech = max(lastFloor * 3.0, 500.0)
        val start = System.currentTimeMillis()
        var spoke = false
        var lastLoud = start
        try {
            while (running) {
                val n = rec.read(chunk, 0, CHUNK)
                if (n <= 0) break
                val now = System.currentTimeMillis()
                val loud = rms(chunk, n) > speech
                if (!spoke) {
                    pre.addLast(chunk.copyOf(n))
                    if (pre.size > 4) pre.removeFirst()                // keep ~0.3 s before you start
                    if (loud) {
                        spoke = true
                        lastLoud = now
                        for (c in pre) for (v in c) out.write2(v)
                    } else if (now - start > 6000) return null
                } else {
                    for (i in 0 until n) out.write2(chunk[i])
                    if (loud) lastLoud = now
                    if (now - lastLoud > 1200 || now - start > 12_000) break
                }
            }
        } finally {
            releaseRecorder()
        }
        return if (spoke) out.toByteArray() else null
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
        @Volatile var pcm: ByteArray? = null                   // what you said after "Eva", when the PC couldn't check
        @Volatile var command: String? = null                  // ...or already transcribed by the PC ("" = just "Eva")
        @Volatile var address: String? = null                  // memory only
        @Volatile var token: String? = null                    // memory only, never written to disk here
        @Volatile var instance: WakeService? = null

        fun start(ctx: Context, address: String, token: String) {
            enabled = true
            EvaLink.set(address, token)                        // messages (v0.3.2) use the same link
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
            command = null
        }
    }
}
