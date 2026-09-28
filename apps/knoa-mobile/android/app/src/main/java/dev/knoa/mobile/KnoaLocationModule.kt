package dev.knoa.mobile

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.ReactPackage
import com.facebook.react.uimanager.ViewManager

private const val NATIVE_FIX_TIMEOUT_MS = 12000L
private const val LAST_KNOWN_MAX_AGE_MS = 5 * 60 * 1000L

/**
 * GMS-independent single fix via the platform LocationManager.
 *
 * expo-location's fused provider needs Google Play services, which many
 * domestic Chinese ROMs (e.g. Honor MagicOS China builds) do not ship.
 * The framework NETWORK_PROVIDER (cell + WiFi) works without GMS and
 * resolves indoors in seconds; GPS is the fallback for open sky.
 */
class KnoaLocationModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  override fun getName(): String = "KnoaLocation"

  @ReactMethod
  fun getFix(promise: Promise) {
    val fine = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION)
    val coarse = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION)
    if (fine != PackageManager.PERMISSION_GRANTED && coarse != PackageManager.PERMISSION_GRANTED) {
      promise.reject("location_permission_denied", "Location permission not granted")
      return
    }
    val manager = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
    if (manager == null) {
      promise.reject("location_unavailable", "No location manager on this device")
      return
    }
    Handler(Looper.getMainLooper()).post {
      try {
        resolveFix(manager, promise)
      } catch (e: Exception) {
        promise.reject("location_failed", e.message ?: "unknown location failure")
      }
    }
  }

  private fun resolveFix(manager: LocationManager, promise: Promise) {
    val mainHandler = Handler(Looper.getMainLooper())
    val liveProviders = listOf(
      LocationManager.NETWORK_PROVIDER,
      LocationManager.GPS_PROVIDER,
      LocationManager.PASSIVE_PROVIDER,
    ).filter { provider ->
      try {
        manager.isProviderEnabled(provider)
      } catch (_: Exception) {
        false
      }
    }
    val now = System.currentTimeMillis()
    val cached = liveProviders.mapNotNull { provider ->
      try {
        manager.getLastKnownLocation(provider)
      } catch (_: SecurityException) {
        null
      } catch (_: Exception) {
        null
      }
    }.filter { now - it.time < LAST_KNOWN_MAX_AGE_MS }
      .minByOrNull { now - it.time }
    if (cached != null) {
      promise.resolve(toMap(cached))
      return
    }

    var settled = false
    fun settle(location: Location?, code: String, message: String) {
      if (settled) return
      settled = true
      if (location != null) promise.resolve(toMap(location))
      else promise.reject(code, message)
    }
    val timeout = Runnable { settle(null, "location_timeout", "Timed out waiting for a fix") }
    mainHandler.postDelayed(timeout, NATIVE_FIX_TIMEOUT_MS)
    val listener = object : LocationListener {
      override fun onLocationChanged(location: Location) {
        try {
          manager.removeUpdates(this)
        } catch (_: Exception) {
        }
        mainHandler.removeCallbacks(timeout)
        settle(location, "", "")
      }
      override fun onProviderDisabled(provider: String) {}
      override fun onProviderEnabled(provider: String) {}
      @Deprecated("Deprecated in Java")
      override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
    }
    val target = liveProviders.firstOrNull { it != LocationManager.PASSIVE_PROVIDER }
      ?: LocationManager.NETWORK_PROVIDER
    try {
      manager.requestSingleUpdate(target, listener, Looper.getMainLooper())
    } catch (e: SecurityException) {
      mainHandler.removeCallbacks(timeout)
      settle(null, "location_permission_denied", "Location permission not granted")
    } catch (e: Exception) {
      mainHandler.removeCallbacks(timeout)
      settle(null, "location_failed", e.message ?: "requestSingleUpdate failed")
    }
  }

  private fun toMap(location: Location) = Arguments.createMap().apply {
    putDouble("latitude", location.latitude)
    putDouble("longitude", location.longitude)
    putDouble("accuracy", location.accuracy.toDouble())
    putString("provider", location.provider ?: "")
    putDouble("timestamp", location.time.toDouble())
  }
}

class KnoaLocationPackage : ReactPackage {
  override fun createNativeModules(reactContext: ReactApplicationContext): List<NativeModule> =
    listOf(KnoaLocationModule(reactContext))

  override fun createViewManagers(reactContext: ReactApplicationContext): List<ViewManager<*, *>> =
    emptyList()
}
