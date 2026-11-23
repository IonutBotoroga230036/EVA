package nl.ionut.eva_android

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Looper
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * Nov 23: where you are, for "remind me when I get home", "where did I park" and (only if you ask) a history.
 *
 * Runs inside the hands-free service (WakeService), so there is no second notification: while "Location" is on
 * in the app's menu and Android allowed it, the phone tells your PC where it is every few minutes, or after you
 * moved about 75 m, using the battery-saving network/fused provider. The PC decides what that means (your places,
 * reminders) and may answer with something to say, which is played here.
 */
object EvaLocation {
    private const val TAG = "EVA-location"
    private const val PREFS = "eva_location"
    private const val EVERY_MS = 3 * 60 * 1000L
    private const val MOVED_M = 75f
    private val pool = Executors.newSingleThreadExecutor()
    @Volatile private var listener: LocationListener? = null

    fun wanted(ctx: Context): Boolean =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("on", false) && allowed(ctx)

    fun allowed(ctx: Context): Boolean =
        ctx.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ctx.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    fun setOn(ctx: Context, on: Boolean) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("on", on).apply()
    }

    fun isOn(ctx: Context): Boolean = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("on", false)

    /** Called by WakeService once it is in the foreground with the location type. */
    fun start(svc: WakeService) {
        if (listener != null || !wanted(svc)) return
        val lm = svc.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val provider = when {
            Build.VERSION.SDK_INT >= 31 && lm.hasProvider(LocationManager.FUSED_PROVIDER) -> LocationManager.FUSED_PROVIDER
            lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
            else -> LocationManager.GPS_PROVIDER
        }
        val l = LocationListener { loc -> report(svc, loc) }
        try {
            lm.requestLocationUpdates(provider, EVERY_MS, MOVED_M, l, Looper.getMainLooper())
            listener = l
            lm.getLastKnownLocation(provider)?.let { report(svc, it) }
            Log.i(TAG, "location on ($provider)")
        } catch (e: SecurityException) {
            Log.w(TAG, "location not allowed: ${e.message}")
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "no location provider: ${e.message}")
        }
    }

    fun stop(ctx: Context) {
        val l = listener ?: return
        (ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager).removeUpdates(l)
        listener = null
    }

    private fun report(svc: WakeService, loc: Location) {
        val address = EvaLink.address ?: return
        val token = EvaLink.token ?: return
        val body = JSONObject().put("lat", loc.latitude).put("lon", loc.longitude).put("acc", loc.accuracy.toDouble())
            .put("ts", loc.time / 1000.0)
        pool.execute {
            val reply = MessageListener.post(address, token, "/api/phone/location", body) ?: return@execute
            val audio = reply.optString("audio", "")
            if (audio.isNotEmpty() && !WakeService.appVisible && !WakeService.overlayOpen) svc.speak(audio)
        }
    }
}
