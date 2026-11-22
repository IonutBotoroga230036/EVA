package nl.ionut.eva_android

import android.app.Notification
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.Parcelable
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * v0.3.2 (milestone 9c): WhatsApp and Instagram messages for E.V.A.
 *
 * Android shows this service the notifications of every app once you allow "Notification access" for E.V.A.;
 * it only looks at WhatsApp, WhatsApp Business and Instagram, and only while "Messages" is on in the app's menu.
 * Each new chat message (sender, text, chat name) goes to your PC over https, nothing else: no chat history,
 * no contacts, no other apps. When the PC says a message matters, she says it here too (if the app isn't open,
 * you're not in a call and Do Not Disturb is off).
 *
 * Replies use the notification's own Reply button (RemoteInput), so they only work while that notification
 * is still there. Nothing is sent unless the PC asks, and the PC asks only after your spoken yes.
 */
class MessageListener : NotificationListenerService() {
    override fun onListenerConnected() {
        instance = this
        Log.i(TAG, "notification access on")
    }

    override fun onListenerDisconnected() {
        if (instance === this) instance = null
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName !in APPS || !enabled(this)) return
        val n = sbn.notification ?: return
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return          // "5 new messages": not a message
        val ex = n.extras ?: return
        val convTitle = ex.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString()
        val title = ex.getCharSequence(Notification.EXTRA_TITLE)?.toString()
        val group = (Build.VERSION.SDK_INT >= 28 && ex.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION)) ||
            convTitle != null
        val conversation = cleanTitle(convTitle ?: title ?: return)
        replyTargets[key(sbn.packageName, conversation)] = sbn
        val messages = messagesOf(ex)
        val out = if (messages.isNotEmpty()) messages.takeLast(3)
        else listOf(Pair(title ?: conversation, ex.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""))
        for ((sender, text) in out) {
            if (sender.isBlank() || text.isBlank()) continue
            send(JSONObject().put("app", sbn.packageName).put("sender", cleanTitle(sender)).put("text", text)
                .put("conversation", conversation).put("group", group))
        }
    }

    /** (sender, text) from a MessagingStyle notification; your own messages (no sender) are skipped. */
    private fun messagesOf(ex: Bundle): List<Pair<String, String>> {
        val arr: Array<Parcelable> = ex.getParcelableArray(Notification.EXTRA_MESSAGES) ?: return emptyList()
        val out = ArrayList<Pair<String, String>>()
        for (p in arr) {
            val b = p as? Bundle ?: continue
            val text = b.getCharSequence("text")?.toString() ?: continue
            var sender = b.getCharSequence("sender")?.toString()
            if (sender == null && Build.VERSION.SDK_INT >= 28) {
                sender = (b.getParcelable<android.app.Person>("sender_person"))?.name?.toString()
            }
            if (sender != null) out.add(Pair(sender, text))
        }
        return out
    }

    private fun send(json: JSONObject) {
        val address = EvaLink.address
        val token = EvaLink.token
        if (address == null || token == null) {                  // the app hasn't run since the phone started
            Log.i(TAG, "a message arrived but E.V.A. isn't linked yet; open the app once")
            return
        }
        pool.execute {
            val reply = post(address, token, "/api/phone/message", json) ?: return@execute
            val audio = reply.optString("audio", "")
            if (audio.isNotEmpty() && shouldSpeak()) play(audio)
        }
    }

    private fun shouldSpeak(): Boolean {
        if (WakeService.appVisible || WakeService.overlayOpen) return false   // the page already says it
        if (currentInterruptionFilter > INTERRUPTION_FILTER_ALL) return false  // Do Not Disturb
        val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        return am.mode != AudioManager.MODE_IN_CALL && am.mode != AudioManager.MODE_IN_COMMUNICATION
    }

    private fun play(b64: String) {
        val f = File(cacheDir, "eva_message.wav")
        f.writeBytes(android.util.Base64.decode(b64, android.util.Base64.DEFAULT))
        val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val attrs = android.media.AudioAttributes.Builder()
            .setUsage(android.media.AudioAttributes.USAGE_ASSISTANT)
            .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SPEECH).build()
        val focus = android.media.AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
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
            Log.w(TAG, "couldn't play the message: ${e.message}")
        } finally {
            mp.release()
            am.abandonAudioFocusRequest(focus)
            f.delete()
        }
    }

    companion object {
        private const val TAG = "EVA-messages"
        val APPS = setOf("com.whatsapp", "com.whatsapp.w4b", "com.instagram.android")
        @Volatile var instance: MessageListener? = null
        private val pool = Executors.newSingleThreadExecutor()
        /** The latest notification of each chat: its Reply button is how a reply gets sent. Memory only. */
        private val replyTargets = java.util.concurrent.ConcurrentHashMap<String, StatusBarNotification>()

        fun enabled(ctx: Context): Boolean =
            ctx.getSharedPreferences("eva_messages", Context.MODE_PRIVATE).getBoolean("on", false)

        fun setEnabled(ctx: Context, on: Boolean) {
            ctx.getSharedPreferences("eva_messages", Context.MODE_PRIVATE).edit().putBoolean("on", on).apply()
            if (!on) replyTargets.clear()
        }

        /** Has Android given E.V.A. "Notification access"? */
        fun accessGranted(ctx: Context): Boolean {
            val list = android.provider.Settings.Secure.getString(ctx.contentResolver, "enabled_notification_listeners")
            return list?.split(":")?.any { it.startsWith(ctx.packageName + "/") } == true
        }

        private fun key(app: String, conversation: String) = app + "|" + conversation.lowercase()

        /** WhatsApp sometimes titles a chat "Mom (2 messages)": the name only. */
        fun cleanTitle(t: String): String = t.replace(Regex("""\s*\(\d+ (?:new )?messages?\)\s*$"""), "").trim()

        /** {"conversation", "app", "text"} from the PC: reply through that chat's notification. */
        fun reply(ctx: Context, args: Map<String, Any?>): Map<String, Any> {
            if (!enabled(ctx)) return mapOf("ok" to false, "error" to "Messages is off in the E.V.A. app menu")
            if (!accessGranted(ctx)) return mapOf("ok" to false, "error" to "E.V.A. doesn't have Notification access")
            val app = args["app"]?.toString() ?: ""
            val conversation = cleanTitle(args["conversation"]?.toString() ?: "")
            val text = args["text"]?.toString()?.trim() ?: ""
            if (text.isEmpty()) return mapOf("ok" to false, "error" to "no text")
            val sbn = (if (app in APPS) replyTargets[key(app, conversation)] else null)
                ?: replyTargets.entries.firstOrNull { it.key.endsWith("|" + conversation.lowercase()) }?.value
                ?: return mapOf("ok" to false, "error" to "the notification is gone")
            val still = instance?.let { svc ->
                try { svc.activeNotifications.any { it.key == sbn.key } } catch (e: Exception) { true }
            } ?: true
            if (!still) {
                replyTargets.values.remove(sbn)
                return mapOf("ok" to false, "error" to "the notification is gone")
            }
            val action = sbn.notification.actions?.firstOrNull { a ->
                a.remoteInputs?.any { it.allowFreeFormInput } == true
            } ?: return mapOf("ok" to false, "error" to "that notification has no reply button")
            val inputs = action.remoteInputs
            val results = Bundle().apply { inputs.forEach { putCharSequence(it.resultKey, text) } }
            val fill = Intent().addFlags(Intent.FLAG_RECEIVER_FOREGROUND)
            RemoteInput.addResultsToIntent(inputs, fill, results)
            if (Build.VERSION.SDK_INT >= 28) RemoteInput.setResultsSource(fill, RemoteInput.SOURCE_FREE_FORM_INPUT)
            return try {
                action.actionIntent.send(ctx, 0, fill)
                Log.i(TAG, "reply sent")
                mapOf("ok" to true)
            } catch (e: Exception) {
                mapOf("ok" to false, "error" to "the notification is gone (${e.javaClass.simpleName})")
            }
        }

        fun post(address: String, token: String, path: String, json: JSONObject): JSONObject? = try {
            val c = URL(address + path).openConnection() as HttpURLConnection
            c.requestMethod = "POST"
            c.doOutput = true
            c.setRequestProperty("Authorization", "Bearer $token")      // https only: EvaLink refuses http
            c.setRequestProperty("Content-Type", "application/json")
            c.connectTimeout = 4000
            c.readTimeout = 30_000
            try {
                c.outputStream.use { it.write(json.toString().toByteArray(Charsets.UTF_8)) }
                if (c.responseCode != 200) null
                else JSONObject(c.inputStream.use { it.readBytes() }.toString(Charsets.UTF_8))
            } finally {
                c.disconnect()
            }
        } catch (e: Exception) {
            Log.w(TAG, "couldn't reach E.V.A.: ${e.message}")
            null
        }
    }
}

/** Where E.V.A. is. Memory only, set by the app when it opens (and by hands-free); https addresses only. */
object EvaLink {
    @Volatile var address: String? = null
        private set
    @Volatile var token: String? = null
        private set

    fun set(address: String, token: String): Boolean {
        if (!address.startsWith("https://") || token.isEmpty()) return false
        this.address = address.trimEnd('/')
        this.token = token
        return true
    }
}
