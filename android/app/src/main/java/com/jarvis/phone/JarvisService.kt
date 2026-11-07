package com.jarvis.phone

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
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.telephony.TelephonyManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.RemoteInput
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject

/**
 * Always-on foreground service: announces calls and texts, takes spoken answers,
 * and keeps the link to the Mac.
 */
class JarvisService : Service() {

    companion object {
        @Volatile var instance: JarvisService? = null
        const val CH_STATUS = "status"
        const val CH_ALERTS = "alerts"
        const val NOTIF_STATUS = 1
        const val NOTIF_CALL = 2
        const val NOTIF_SMS = 3
        const val EXTRA_FROM_UI = "from_ui"
        const val ACTION_ANSWER = "com.jarvis.phone.ANSWER"
        const val ACTION_REJECT = "com.jarvis.phone.REJECT"
        const val ACTION_REJECT_TEXT = "com.jarvis.phone.REJECT_TEXT"
        const val ACTION_SMS_REPLY = "com.jarvis.phone.SMS_REPLY"
        const val ACTION_RELINK = "com.jarvis.phone.RELINK"
        const val KEY_REPLY = "reply_text"
        const val EXTRA_NUMBER = "number"

        fun start(ctx: Context, fromUi: Boolean = false) {
            val i = Intent(ctx, JarvisService::class.java).putExtra(EXTRA_FROM_UI, fromUi)
            try {
                ContextCompat.startForegroundService(ctx, i)
            } catch (e: Exception) {
                Log.w("Jarvis", "can't start service now: ${e.message}")
            }
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var prefs: Prefs
    private lateinit var speaker: Speaker
    private lateinit var listener: Listener
    private lateinit var link: MacLink

    private var callState = TelephonyManager.EXTRA_STATE_IDLE
    private var ringingNumber: String? = null
    private var callFlowActive = false
    private var ringerMuted = false
    private var pendingUnknownCall: Runnable? = null
    private var conversationBusy = false

    // ------------------------------------------------------------------ lifecycle
    override fun onCreate() {
        super.onCreate()
        instance = this
        prefs = Prefs(this)
        speaker = Speaker(this, prefs)
        listener = Listener(this)
        createChannels()
        goForeground(fromUi = false)
        link = MacLink(prefs, ::handleCommand) { updateStatus() }
        link.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.getBooleanExtra(EXTRA_FROM_UI, false) == true) goForeground(fromUi = true)
        intent?.action?.let { handleAction(intent) }
        return START_STICKY
    }

    override fun onDestroy() {
        instance = null
        link.stop()
        listener.cancel()
        speaker.shutdown()
        unmuteRinger()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun goForeground(fromUi: Boolean) {
        var type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        // The microphone type keeps voice replies working in the background; Android only allows
        // it when the app is on screen and has the permission.
        if (fromUi && Build.VERSION.SDK_INT >= 30 && has(Manifest.permission.RECORD_AUDIO)) {
            type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        }
        try {
            ServiceCompat.startForeground(this, NOTIF_STATUS, statusNotification(), type)
        } catch (e: Exception) {
            Log.w("Jarvis", "startForeground with type $type failed: ${e.message}")
            ServiceCompat.startForeground(this, NOTIF_STATUS, statusNotification(),
                if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0)
        }
    }

    fun relink() {
        link.restart()
        updateStatus()
    }

    private fun has(p: String) = ContextCompat.checkSelfPermission(this, p) == PackageManager.PERMISSION_GRANTED

    // ------------------------------------------------------------------ calls
    fun onCallState(state: String, number: String?) {
        if (state == TelephonyManager.EXTRA_STATE_RINGING) {
            callState = state
            if (callFlowActive) return
            if (number.isNullOrBlank()) {
                // Android sends PHONE_STATE twice; only the second carries the number. Wait briefly.
                if (pendingUnknownCall == null) {
                    pendingUnknownCall = Runnable { pendingUnknownCall = null; if (!callFlowActive && callState == state) startCallFlow(null) }
                    main.postDelayed(pendingUnknownCall!!, 700)
                }
                return
            }
            pendingUnknownCall?.let { main.removeCallbacks(it) }
            pendingUnknownCall = null
            startCallFlow(number)
        } else {
            callState = state
            pendingUnknownCall?.let { main.removeCallbacks(it) }
            pendingUnknownCall = null
            if (callFlowActive) endCallFlow()
        }
    }

    private fun startCallFlow(number: String?) {
        callFlowActive = true
        ringingNumber = number
        val contact = PhoneUtils.nameForNumber(this, number)
        val spoken = contact ?: PhoneUtils.spokenNumber(number)
        link.sendEvent("call", mapOf("name" to (contact ?: number ?: "private number"), "number" to (number ?: "")))
        showCallNotification(contact ?: number ?: "Private number")
        if (!prefs.announceCalls) return
        conversationBusy = true
        listener.cancel()
        muteRinger()
        speaker.say("${prefs.title}, you have an incoming call from $spoken.") {
            if (!callFlowActive) return@say
            if (prefs.voiceReplies && listener.available && has(Manifest.permission.RECORD_AUDIO)) {
                listener.listen { heard ->
                    if (!callFlowActive) return@listen
                    when (Commands.callAction(heard)) {
                        Commands.CallAction.ANSWER -> answer()
                        Commands.CallAction.REJECT -> reject(withText = false)
                        Commands.CallAction.REJECT_AND_TEXT -> reject(withText = true)
                        Commands.CallAction.NONE -> unmuteRinger()  // let it ring normally
                    }
                }
            } else {
                unmuteRinger()
            }
        }
    }

    private fun endCallFlow() {
        callFlowActive = false
        conversationBusy = false
        listener.cancel()
        speaker.stop()
        unmuteRinger()
        nm().cancel(NOTIF_CALL)
    }

    private fun answer() {
        listener.cancel(); speaker.stop(); unmuteRinger()
        if (!PhoneUtils.answerCall(this)) toastNote("Couldn't answer - grant the 'answer phone calls' permission.")
    }

    private fun reject(withText: Boolean) {
        val number = ringingNumber
        listener.cancel(); speaker.stop(); unmuteRinger()
        val ok = PhoneUtils.rejectCall(this)
        if (!ok) toastNote("Couldn't reject - grant the 'answer phone calls' permission.")
        if (withText && !number.isNullOrBlank() && prefs.busyReply.isNotBlank()) {
            try { PhoneUtils.sendSms(this, number, prefs.busyReply) } catch (e: Exception) { Log.w("Jarvis", "busy SMS failed", e) }
        }
        endCallFlow()
    }

    private fun muteRinger() {
        try {
            getSystemService(AudioManager::class.java).adjustStreamVolume(AudioManager.STREAM_RING, AudioManager.ADJUST_MUTE, 0)
            ringerMuted = true
        } catch (e: SecurityException) { /* Do Not Disturb blocks it; announce over the ringtone */ }
    }

    private fun unmuteRinger() {
        if (!ringerMuted) return
        try {
            getSystemService(AudioManager::class.java).adjustStreamVolume(AudioManager.STREAM_RING, AudioManager.ADJUST_UNMUTE, 0)
        } catch (_: SecurityException) {}
        ringerMuted = false
    }

    // ------------------------------------------------------------------ SMS
    fun onSms(number: String, body: String) {
        val contact = PhoneUtils.nameForNumber(this, number)
        link.sendEvent("sms", mapOf("name" to (contact ?: number), "number" to number, "body" to body))
        showSmsNotification(number, contact ?: number, body)
        if (!prefs.announceSms || callState != TelephonyManager.EXTRA_STATE_IDLE || conversationBusy) return
        conversationBusy = true
        val who = contact ?: PhoneUtils.spokenNumber(number)
        speaker.say("${prefs.title}, a message from $who. $body") {
            if (!prefs.voiceReplies || !listener.available || !has(Manifest.permission.RECORD_AUDIO)) { conversationBusy = false; return@say }
            speaker.say("Would you like to reply?") {
                listener.listen { heard ->
                    val inline = Commands.inlineReply(heard)
                    when {
                        inline != null -> confirmReply(number, contact ?: number, inline)
                        Commands.yesNo(heard) == Commands.YesNo.YES -> askForReply(number, contact ?: number)
                        else -> conversationBusy = false
                    }
                }
            }
        }
    }

    private fun askForReply(number: String, name: String) {
        speaker.say("What would you like to say?") {
            listener.listen(longForm = true) { heard ->
                val text = heard.firstOrNull()?.trim()
                if (text.isNullOrBlank()) {
                    speaker.say("I didn't catch that. Use the reply button on the notification.") { conversationBusy = false }
                } else confirmReply(number, name, text.replaceFirstChar { it.uppercase() })
            }
        }
    }

    private fun confirmReply(number: String, name: String, text: String) {
        speaker.say("You said: $text. Shall I send it?") {
            listener.listen { heard ->
                when (Commands.yesNo(heard)) {
                    Commands.YesNo.YES -> {
                        try {
                            PhoneUtils.sendSms(this, number, text)
                            nm().cancel(NOTIF_SMS)
                            speaker.say("Sent.") { conversationBusy = false }
                        } catch (e: Exception) {
                            speaker.say("Sending failed, I'm afraid.") { conversationBusy = false }
                        }
                    }
                    Commands.YesNo.NO -> speaker.say("Very well, not sent.") { conversationBusy = false }
                    Commands.YesNo.UNKNOWN -> speaker.say("I'll leave it unsent.") { conversationBusy = false }
                }
            }
        }
    }

    // ------------------------------------------------------------------ notification actions
    fun handleAction(intent: Intent) {
        when (intent.action) {
            ACTION_ANSWER -> answer()
            ACTION_REJECT -> reject(withText = false)
            ACTION_REJECT_TEXT -> reject(withText = true)
            ACTION_RELINK -> relink()
            ACTION_SMS_REPLY -> {
                val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(KEY_REPLY)?.toString()
                val number = intent.getStringExtra(EXTRA_NUMBER)
                if (!text.isNullOrBlank() && !number.isNullOrBlank()) {
                    try {
                        PhoneUtils.sendSms(this, number, text)
                        nm().notify(NOTIF_SMS, NotificationCompat.Builder(this, CH_ALERTS)
                            .setSmallIcon(android.R.drawable.ic_menu_send).setContentText("Reply sent").setTimeoutAfter(3000).build())
                    } catch (e: Exception) {
                        toastNote("Sending failed: ${e.message}")
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------ commands from the Mac
    private fun handleCommand(msg: JSONObject): JSONObject {
        val ok = { r: String -> JSONObject().put("ok", true).put("result", r) }
        val err = { e: String -> JSONObject().put("ok", false).put("error", e) }
        return when (msg.optString("cmd")) {
            "status" -> ok("Phone connected (${Build.MODEL}), battery ${PhoneUtils.battery(this)}%.")
            "send_sms" -> {
                if (!has(Manifest.permission.SEND_SMS)) return err("SMS permission not granted on the phone.")
                val c = PhoneUtils.findContact(this, msg.optString("contact")) ?: return err("No contact matching '${msg.optString("contact")}' on the phone.")
                PhoneUtils.sendSms(this, c.number, msg.optString("text"))
                ok("Text sent to ${c.name}.")
            }
            "call" -> {
                if (!has(Manifest.permission.CALL_PHONE)) return err("Call permission not granted on the phone.")
                val c = PhoneUtils.findContact(this, msg.optString("contact")) ?: return err("No contact matching '${msg.optString("contact")}' on the phone.")
                PhoneUtils.placeCall(this, c.number)
                ok("Calling ${c.name} on your phone.")
            }
            "recent_sms" -> {
                if (!has(Manifest.permission.READ_SMS)) return err("SMS permission not granted on the phone.")
                ok(PhoneUtils.recentSms(this, msg.optString("contact"), msg.optInt("limit", 5)))
            }
            else -> err("Unknown command ${msg.optString("cmd")}")
        }
    }

    // ------------------------------------------------------------------ notifications
    private fun nm() = getSystemService(NotificationManager::class.java)

    private fun createChannels() {
        nm().createNotificationChannel(NotificationChannel(CH_STATUS, "Jarvis status", NotificationManager.IMPORTANCE_MIN))
        nm().createNotificationChannel(NotificationChannel(CH_ALERTS, "Calls and messages", NotificationManager.IMPORTANCE_HIGH))
    }

    private fun openAppIntent() = PendingIntent.getActivity(
        this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun statusNotification(): Notification {
        val linked = if (::link.isInitialized && link.connected) "Linked to your Mac" else if (prefs.linked) "Mac not reachable" else "Not linked to a Mac"
        return NotificationCompat.Builder(this, CH_STATUS)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Jarvis is on duty")
            .setContentText("Announcing calls and texts · $linked")
            .setOngoing(true)
            .setContentIntent(openAppIntent())
            .build()
    }

    fun updateStatus() = nm().notify(NOTIF_STATUS, statusNotification())

    val macConnected: Boolean get() = ::link.isInitialized && link.connected

    private fun actionIntent(action: String, code: Int, mutable: Boolean = false, number: String? = null): PendingIntent {
        val i = Intent(this, ActionReceiver::class.java).setAction(action)
        if (number != null) i.putExtra(EXTRA_NUMBER, number)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (mutable) PendingIntent.FLAG_MUTABLE else PendingIntent.FLAG_IMMUTABLE)
        return PendingIntent.getBroadcast(this, code, i, flags)
    }

    private fun showCallNotification(who: String) {
        val n = NotificationCompat.Builder(this, CH_ALERTS)
            .setSmallIcon(android.R.drawable.sym_call_incoming)
            .setContentTitle("Incoming call")
            .setContentText(who)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .addAction(0, "Answer", actionIntent(ACTION_ANSWER, 10))
            .addAction(0, "Reject", actionIntent(ACTION_REJECT, 11))
            .addAction(0, "Reject + text", actionIntent(ACTION_REJECT_TEXT, 12))
            .setAutoCancel(true)
            .build()
        nm().notify(NOTIF_CALL, n)
    }

    private fun showSmsNotification(number: String, who: String, body: String) {
        val remote = RemoteInput.Builder(KEY_REPLY).setLabel("Reply to $who").build()
        val reply = NotificationCompat.Action.Builder(android.R.drawable.ic_menu_send, "Reply",
            actionIntent(ACTION_SMS_REPLY, 20, mutable = true, number = number))
            .addRemoteInput(remote).setAllowGeneratedReplies(true).build()
        val n = NotificationCompat.Builder(this, CH_ALERTS)
            .setSmallIcon(android.R.drawable.sym_action_chat)
            .setContentTitle(who)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .addAction(reply)
            .setAutoCancel(true)
            .build()
        nm().notify(NOTIF_SMS, n)
    }

    private fun toastNote(text: String) {
        nm().notify(99, NotificationCompat.Builder(this, CH_ALERTS)
            .setSmallIcon(android.R.drawable.stat_notify_error).setContentTitle("Jarvis").setContentText(text)
            .setContentIntent(openAppIntent()).setAutoCancel(true).build())
    }

    /** Used by the "Test" button in the app. */
    fun testAnnouncement() {
        speaker.say("${prefs.title}, you have an incoming call from Alex. This is only a test.")
    }
}
