package nl.ionut.eva_android

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.KeyguardManager
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONObject

/**
 * v0.3 9b: the overlay after "Eva", over any app or the lock screen: the Core visual (edge glow + orb, from your
 * Claude Design file) with E.V.A.'s real page logic behind it (server route /overlay). Transparent everywhere
 * else, so you still see what you were doing. It closes itself a few seconds after the conversation ends.
 *
 * Locked phone: the page tells your PC, which then only does harmless things (time, weather, music, lights,
 * timers, adding a meeting / reminder / shopping item). Anything private asks you to unlock first, and carries
 * on by itself once you have.
 */
class OverlayActivity : Activity() {
    private var web: WebView? = null
    private val main = Handler(Looper.getMainLooper())
    private var lastLocked: Boolean? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        // edge to edge, behind the gesture bar and the camera cutout too (Nov 22: the bottom bar stayed visible)
        window.addFlags(WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false)
            window.attributes = window.attributes.apply {
                layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            window.isNavigationBarContrastEnforced = false
            window.isStatusBarContrastEnforced = false
        }
        val address = WakeService.address
        val token = WakeService.token
        if (address == null || token == null) {
            finish()
            return
        }
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setCookie(address, "eva_token=$token; Path=/; Secure")
            flush()
        }
        val w = WebView(this)
        web = w
        w.setBackgroundColor(Color.TRANSPARENT)
        w.settings.javaScriptEnabled = true
        w.settings.domStorageEnabled = true
        w.settings.mediaPlaybackRequiresUserGesture = false                 // her voice, without a tap
        val audio = getSharedPreferences("eva_wake", MODE_PRIVATE).getString("audio", "media")
        w.settings.userAgentString = w.settings.userAgentString + " EVA-Android/0.3 overlay" +
            if (audio == "call") " audio=call" else ""
        val host = Uri.parse(address).host
        w.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                val micOk = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
                val onlyMic = request.resources.all { it == PermissionRequest.RESOURCE_AUDIO_CAPTURE }
                if (micOk && onlyMic && request.origin.host == host) request.grant(request.resources) else request.deny()
            }
        }
        w.addJavascriptInterface(Bridge(), "EvaOverlay")
        // an instant glow while the page loads, so you see she heard you straight away
        val glow = android.view.View(this).apply {
            background = android.graphics.drawable.GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                setStroke((6 * resources.displayMetrics.density).toInt(), Color.argb(170, 168, 85, 247))
                cornerRadius = 48 * resources.displayMetrics.density
            }
        }
        val frame = android.widget.FrameLayout(this)
        frame.addView(glow)
        frame.addView(w)
        w.alpha = 0f
        w.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                request.url.host != host                                        // your PC only
            override fun onPageFinished(view: WebView, url: String) {
                view.animate().alpha(1f).setDuration(250).withEndAction { glow.visibility = android.view.View.GONE }
            }
        }
        setContentView(frame)
        w.loadUrl("$address/overlay", mapOf("Authorization" to "Bearer $token"))
    }

    override fun onResume() {
        super.onResume()
        sendLockState()
    }

    override fun onDestroy() {
        WakeService.overlayClosed()
        web?.let {
            it.loadUrl("about:blank")
            it.destroy()
        }
        web = null
        super.onDestroy()
    }

    private fun locked(): Boolean = (getSystemService(KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked

    private fun sendLockState() {
        val now = locked()
        if (now == lastLocked) return
        lastLocked = now
        js("window.dispatchEvent(new CustomEvent('eva:device-state', {detail: {locked: $now}}))")
    }

    private fun js(code: String) = main.post { web?.evaluateJavascript(code, null) }

    /** What the page can ask of the phone. Called on a background thread by the WebView. */
    inner class Bridge {
        @JavascriptInterface
        fun isLocked(): Boolean = locked()

        /** The page is up: hand over what you said after "Eva" as soon as the wake service has finished it. */
        @JavascriptInterface
        fun ready() {
            Thread {
                val until = System.currentTimeMillis() + 10_000
                while (WakeService.pcm == null && WakeService.command == null && WakeService.overlayOpen &&
                    System.currentTimeMillis() < until) {
                    Thread.sleep(50)
                }
                val cmd = WakeService.command
                val detail = if (cmd != null) JSONObject().put("text", cmd)          // the PC already transcribed it
                    else JSONObject().put("pcm", Base64.encodeToString(WakeService.pcm ?: ByteArray(0), Base64.NO_WRAP))
                js("window.dispatchEvent(new CustomEvent('eva:pcm', {detail: $detail}))")
            }.start()
        }

        @JavascriptInterface
        fun close() = main.post { finish() }

        @JavascriptInterface
        fun unlock() = main.post {
            val km = getSystemService(KEYGUARD_SERVICE) as KeyguardManager
            if (!km.isKeyguardLocked) {
                sendLockState()
                return@post
            }
            km.requestDismissKeyguard(this@OverlayActivity, object : KeyguardManager.KeyguardDismissCallback() {
                override fun onDismissSucceeded() = sendLockState()          // the PC carries on with what you asked
            })
        }
    }
}
