package nl.ionut.eva_android

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.provider.Settings
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Nov 23: "she only put it in my chat with my mom but didn't send it".
 *
 * When E.V.A. opens a WhatsApp chat with a message you approved by voice, this presses Send for you. It does that
 * only within a short window after she opened the chat, only in WhatsApp, and only when the text box holds
 * exactly the message she prepared, so it can never send anything else or act on its own.
 * It works only after you turn it on: Android Settings > Accessibility > E.V.A. send > on (for an app
 * installed outside the Play Store, first: App info > three dots > Allow restricted settings).
 */
class EvaSend : AccessibilityService() {
    override fun onServiceConnected() {
        Log.i(TAG, "auto-send on")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val text = pendingText ?: return
        if (System.currentTimeMillis() > pendingUntil) {
            pendingText = null
            return
        }
        val pkg = event?.packageName?.toString() ?: return
        if (!pkg.startsWith("com.whatsapp")) return
        val root = rootInActiveWindow ?: return
        val entry = root.findAccessibilityNodeInfosByViewId("$pkg:id/entry").firstOrNull()
        val typed = entry?.text?.toString()?.trim()
        if (typed != text.trim()) return                    // not (yet) our message in the box: do nothing
        val send = root.findAccessibilityNodeInfosByViewId("$pkg:id/send").firstOrNull()
            ?: findByDescription(root, setOf("send", "trimite", "verzenden"))
            ?: return
        if (send.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            Log.i(TAG, "pressed Send")
            pendingText = null
        }
    }

    private fun findByDescription(node: AccessibilityNodeInfo, names: Set<String>): AccessibilityNodeInfo? {
        val d = node.contentDescription?.toString()?.lowercase()
        if (d != null && d in names && node.isClickable) return node
        for (i in 0 until node.childCount) {
            val c = node.getChild(i) ?: continue
            findByDescription(c, names)?.let { return it }
        }
        return null
    }

    override fun onInterrupt() {}

    companion object {
        private const val TAG = "EVA-send"
        @Volatile var pendingText: String? = null
        @Volatile var pendingUntil = 0L

        /** Turned on in Android's Accessibility settings? */
        fun enabled(ctx: Context): Boolean {
            val list = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
            return list?.split(":")?.any { it.startsWith(ctx.packageName + "/") } == true
        }

        /** Press Send for exactly this text if it shows up in WhatsApp soon (longer while the phone is locked). */
        fun expect(text: String, locked: Boolean) {
            pendingText = text
            pendingUntil = System.currentTimeMillis() + if (locked) 120_000 else 15_000
        }
    }
}
