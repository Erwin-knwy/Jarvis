package com.jarvis.phone

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

/** Settings screen: permissions, link to the Mac, voice options. Built in code (no XML). */
class MainActivity : Activity() {

    private lateinit var prefs: Prefs
    private lateinit var status: TextView
    private lateinit var titleField: EditText
    private lateinit var urlField: EditText
    private lateinit var tokenField: EditText
    private lateinit var pinField: EditText
    private lateinit var busyField: EditText

    private val permissions: Array<String>
        get() = buildList {
            add(Manifest.permission.READ_PHONE_STATE)
            add(Manifest.permission.READ_CALL_LOG)
            add(Manifest.permission.ANSWER_PHONE_CALLS)
            add(Manifest.permission.CALL_PHONE)
            add(Manifest.permission.READ_CONTACTS)
            add(Manifest.permission.RECEIVE_SMS)
            add(Manifest.permission.READ_SMS)
            add(Manifest.permission.SEND_SMS)
            add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
        }.toTypedArray()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        buildUi()
        handlePairing(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handlePairing(intent)
    }

    override fun onResume() {
        super.onResume()
        JarvisService.start(this, fromUi = true)
        window.decorView.postDelayed({ refreshStatus() }, 600)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        JarvisService.start(this, fromUi = true)
        refreshStatus()
    }

    private fun handlePairing(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme != "jarvis" || data.host != "pair") return
        val url = data.getQueryParameter("url").orEmpty()
        val token = data.getQueryParameter("token").orEmpty()
        if (url.isBlank() || token.isBlank()) return
        prefs.serverUrl = url
        prefs.token = token
        prefs.certPin = data.getQueryParameter("pin").orEmpty()
        urlField.setText(prefs.serverUrl); tokenField.setText(prefs.token); pinField.setText(prefs.certPin)
        JarvisService.instance?.relink()
        Toast.makeText(this, "Linked to your Mac", Toast.LENGTH_LONG).show()
        window.decorView.postDelayed({ refreshStatus() }, 2500)
    }

    private fun missingPermissions() = permissions.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }

    @SuppressLint("SetTextI18n")
    private fun refreshStatus() {
        val missing = missingPermissions()
        val pm = getSystemService(PowerManager::class.java)
        val battery = pm.isIgnoringBatteryOptimizations(packageName)
        val svc = JarvisService.instance
        status.text = buildString {
            append(if (svc != null) "● Jarvis is running\n" else "○ Jarvis is not running\n")
            append(if (missing.isEmpty()) "✓ All permissions granted\n" else "✗ Missing: ${missing.joinToString { it.substringAfterLast('.') }}\n")
            append(if (battery) "✓ Battery optimisation off\n" else "✗ Battery optimisation is on (Android may stop Jarvis)\n")
            append(
                when {
                    !prefs.linked -> "○ Not linked to a Mac (optional)"
                    svc?.macConnected == true -> "✓ Linked to your Mac"
                    else -> "… Mac not reachable right now"
                }
            )
        }
    }

    // ------------------------------------------------------------------ UI
    private fun buildUi() {
        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad * 2, pad, pad * 2)
            setBackgroundColor(Color.parseColor("#05080D"))
        }
        fun label(text: String, size: Float = 14f, color: String = "#6F8399", bold: Boolean = false) =
            TextView(this).apply {
                this.text = text; textSize = size; setTextColor(Color.parseColor(color))
                if (bold) typeface = Typeface.DEFAULT_BOLD
                setPadding(0, pad / 2, 0, pad / 4)
            }.also { root.addView(it) }
        fun field(hint: String, value: String, password: Boolean = false) = EditText(this).apply {
            this.hint = hint; setText(value); setTextColor(Color.WHITE); setHintTextColor(Color.GRAY)
            inputType = if (password) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }.also { root.addView(it) }
        fun button(text: String, onClick: () -> Unit) = Button(this).apply {
            this.text = text; setOnClickListener { onClick() }
        }.also { root.addView(it) }
        fun switch(text: String, value: Boolean, onChange: (Boolean) -> Unit) = Switch(this).apply {
            this.text = text; isChecked = value; setTextColor(Color.WHITE)
            setPadding(0, pad / 3, 0, pad / 3)
            setOnCheckedChangeListener { _, b -> onChange(b) }
        }.also { root.addView(it) }

        label("J.A.R.V.I.S.", 26f, "#8BE9FF", bold = true).gravity = Gravity.CENTER_HORIZONTAL
        status = label("", 15f, "#D8E6F3")

        button("1 · Grant permissions") {
            val missing = missingPermissions()
            if (missing.isEmpty()) Toast.makeText(this, "All granted", Toast.LENGTH_SHORT).show()
            else requestPermissions(missing.toTypedArray(), 1)
        }
        button("2 · Keep Jarvis running (battery)") {
            @SuppressLint("BatteryLife")
            val i = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
            startActivity(i)
        }
        label("If SMS or call permissions are greyed out: Settings → Apps → Jarvis Phone → ⋮ → \"Allow restricted settings\", then try again.", 12f)
        button("App settings") {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
        }

        label("How should Jarvis address you?", bold = true)
        titleField = field("Sir", prefs.title)

        label("Announcements", bold = true)
        switch("Announce incoming calls", prefs.announceCalls) { prefs.announceCalls = it }
        switch("Read out text messages", prefs.announceSms) { prefs.announceSms = it }
        switch("Listen for my answer (answer / reject / reply)", prefs.voiceReplies) { prefs.voiceReplies = it }
        switch("Use Jarvis's voice from the Mac when reachable", prefs.useMacVoice) { prefs.useMacVoice = it }
        label("Auto-reply for \"reject and text\"")
        busyField = field("Can't talk right now…", prefs.busyReply)

        label("Link to your Mac (optional)", bold = true)
        label("Easiest: on the Mac run `jarvis phone-setup`, open the link on this phone and tap \"Link to this Mac\".", 12f)
        urlField = field("https://192.168.x.x:8765", prefs.serverUrl)
        tokenField = field("token", prefs.token, password = true)
        pinField = field("certificate pin (filled by the link)", prefs.certPin)

        button("Save") {
            prefs.title = titleField.text.toString().ifBlank { "Sir" }
            prefs.busyReply = busyField.text.toString()
            prefs.serverUrl = urlField.text.toString().trim()
            prefs.token = tokenField.text.toString().trim()
            prefs.certPin = pinField.text.toString().trim()
            JarvisService.instance?.relink()
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
            window.decorView.postDelayed({ refreshStatus() }, 2500)
        }
        button("Test the announcement") {
            prefs.title = titleField.text.toString().ifBlank { "Sir" }
            JarvisService.instance?.testAnnouncement()
                ?: Toast.makeText(this, "Jarvis isn't running yet", Toast.LENGTH_SHORT).show()
        }

        val scroll = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#05080D"))
            addView(root)
            isFillViewport = true
        }
        setContentView(scroll)
        window.statusBarColor = Color.parseColor("#05080D")
        status.visibility = View.VISIBLE
    }
}
