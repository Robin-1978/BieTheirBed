package dev.knoa.mobile

import androidx.appcompat.app.AppCompatDelegate
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.UiThreadUtil
import com.facebook.react.uimanager.ViewManager
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule

private const val THEME_PREFS = "knoa_ui"
private const val THEME_MODE = "theme_mode"

internal fun appCompatThemeMode(mode: String): Int = when (mode) {
  "light" -> AppCompatDelegate.MODE_NIGHT_NO
  "dark" -> AppCompatDelegate.MODE_NIGHT_YES
  else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
}

internal fun storedThemeMode(context: android.content.Context): String =
  context.getSharedPreferences(THEME_PREFS, android.content.Context.MODE_PRIVATE)
    .getString(THEME_MODE, "system")
    ?.takeIf { it == "system" || it == "light" || it == "dark" }
    ?: "system"

class KnoaThemeModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  override fun getName(): String = "KnoaTheme"

  @ReactMethod
  fun getMode(promise: Promise) {
    promise.resolve(storedThemeMode(context))
  }

  @ReactMethod
  fun setMode(mode: String, promise: Promise) {
    if (mode != "system" && mode != "light" && mode != "dark") {
      promise.reject("invalid_theme_mode", "Unsupported theme mode")
      return
    }
    context.getSharedPreferences(THEME_PREFS, android.content.Context.MODE_PRIVATE)
      .edit()
      .putString(THEME_MODE, mode)
      .apply()
    UiThreadUtil.runOnUiThread {
      AppCompatDelegate.setDefaultNightMode(appCompatThemeMode(mode))
      promise.resolve(null)
    }
  }
}

class KnoaThemePackage : ReactPackage {
  override fun createNativeModules(reactContext: ReactApplicationContext): List<NativeModule> =
    listOf(KnoaThemeModule(reactContext))

  override fun createViewManagers(reactContext: ReactApplicationContext): List<ViewManager<*, *>> =
    emptyList()
}
