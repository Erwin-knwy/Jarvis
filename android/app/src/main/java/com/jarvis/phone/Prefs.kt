package com.jarvis.phone

import android.content.Context
import android.content.SharedPreferences

/** All user settings, stored in SharedPreferences. */
class Prefs(context: Context) {
    private val p: SharedPreferences = context.getSharedPreferences("jarvis", Context.MODE_PRIVATE)

    var title: String
        get() = p.getString("title", "Sir") ?: "Sir"
        set(v) = p.edit().putString("title", v).apply()

    /** e.g. https://192.168.1.20:8765 or https://mac.tailnet.ts.net */
    var serverUrl: String
        get() = p.getString("server_url", "") ?: ""
        set(v) = p.edit().putString("server_url", v.trimEnd('/')).apply()

    var token: String
        get() = p.getString("token", "") ?: ""
        set(v) = p.edit().putString("token", v).apply()

    /** SHA-256 hex of the Mac's self-signed certificate (empty = normal certificate checks). */
    var certPin: String
        get() = p.getString("cert_pin", "") ?: ""
        set(v) = p.edit().putString("cert_pin", v.lowercase()).apply()

    var announceCalls: Boolean
        get() = p.getBoolean("announce_calls", true)
        set(v) = p.edit().putBoolean("announce_calls", v).apply()

    var announceSms: Boolean
        get() = p.getBoolean("announce_sms", true)
        set(v) = p.edit().putBoolean("announce_sms", v).apply()

    var voiceReplies: Boolean
        get() = p.getBoolean("voice_replies", true)
        set(v) = p.edit().putBoolean("voice_replies", v).apply()

    var useMacVoice: Boolean
        get() = p.getBoolean("use_mac_voice", true)
        set(v) = p.edit().putBoolean("use_mac_voice", v).apply()

    var busyReply: String
        get() = p.getString("busy_reply", "Can't talk right now, I'll call you back.") ?: ""
        set(v) = p.edit().putString("busy_reply", v).apply()

    val linked: Boolean get() = serverUrl.isNotBlank() && token.isNotBlank()
}
