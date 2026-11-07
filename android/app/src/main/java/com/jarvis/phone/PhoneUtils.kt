package com.jarvis.phone

import android.annotation.SuppressLint
import android.content.Context
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.provider.ContactsContract
import android.provider.Telephony
import android.telecom.TelecomManager
import android.telephony.SmsManager
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Contacts, SMS, calls and battery helpers. All need their runtime permissions granted. */
object PhoneUtils {

    data class Contact(val name: String, val number: String)

    /** Contact display name for a phone number, or null if unknown. */
    fun nameForNumber(ctx: Context, number: String?): String? {
        if (number.isNullOrBlank()) return null
        return try {
            val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
            ctx.contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME), null, null, null)
                ?.use { c -> if (c.moveToFirst()) c.getString(0) else null }
        } catch (e: SecurityException) {
            null
        }
    }

    /** Find a contact by (fuzzy) name, or accept a raw number. */
    fun findContact(ctx: Context, query: String): Contact? {
        val q = query.trim()
        if (q.isEmpty()) return null
        if (q.count { it.isDigit() } >= 5 && q.all { it.isDigit() || it in "+ -()" }) {
            return Contact(nameForNumber(ctx, q) ?: q, q)
        }
        val all = mutableListOf<Contact>()
        try {
            ctx.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                    ContactsContract.CommonDataKinds.Phone.NUMBER,
                    ContactsContract.CommonDataKinds.Phone.IS_SUPER_PRIMARY,
                ),
                null, null, null,
            )?.use { c ->
                while (c.moveToNext()) {
                    val name = c.getString(0) ?: continue
                    val num = c.getString(1) ?: continue
                    all.add(Contact(name, num))
                }
            }
        } catch (e: SecurityException) {
            return null
        }
        val ql = q.lowercase(Locale.ROOT)
        return all.firstOrNull { it.name.lowercase(Locale.ROOT) == ql }
            ?: all.firstOrNull { it.name.lowercase(Locale.ROOT).startsWith(ql) }
            ?: all.firstOrNull { it.name.lowercase(Locale.ROOT).contains(ql) }
            ?: all.maxByOrNull { similarity(ql, it.name.lowercase(Locale.ROOT)) }
                ?.takeIf { similarity(ql, it.name.lowercase(Locale.ROOT)) >= 0.6 }
    }

    private fun similarity(a: String, b: String): Double {
        if (a.isEmpty() || b.isEmpty()) return 0.0
        val dp = Array(a.length + 1) { IntArray(b.length + 1) }
        for (i in 0..a.length) dp[i][0] = i
        for (j in 0..b.length) dp[0][j] = j
        for (i in 1..a.length) for (j in 1..b.length) {
            dp[i][j] = minOf(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
        }
        return 1.0 - dp[a.length][b.length].toDouble() / maxOf(a.length, b.length)
    }

    fun sendSms(ctx: Context, number: String, text: String) {
        val sms: SmsManager = if (Build.VERSION.SDK_INT >= 31) ctx.getSystemService(SmsManager::class.java)
        else @Suppress("DEPRECATION") SmsManager.getDefault()
        val parts = sms.divideMessage(text)
        if (parts.size > 1) sms.sendMultipartTextMessage(number, null, parts, null, null)
        else sms.sendTextMessage(number, null, text, null, null)
    }

    fun recentSms(ctx: Context, contact: String, limit: Int): String {
        val filterNumber = if (contact.isNotBlank()) findContact(ctx, contact)?.number?.filter { it.isDigit() }?.takeLast(9) else null
        if (contact.isNotBlank() && filterNumber == null) return "No contact matching '$contact'."
        val out = mutableListOf<String>()
        val fmt = SimpleDateFormat("EEE HH:mm", Locale.getDefault())
        ctx.contentResolver.query(
            Telephony.Sms.Inbox.CONTENT_URI,
            arrayOf(Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE),
            null, null, "${Telephony.Sms.DATE} DESC",
        )?.use { c ->
            while (c.moveToNext() && out.size < limit) {
                val addr = c.getString(0) ?: ""
                if (filterNumber != null && !addr.filter { it.isDigit() }.endsWith(filterNumber)) continue
                val who = nameForNumber(ctx, addr) ?: addr
                out.add("[${fmt.format(Date(c.getLong(2)))}] $who: ${c.getString(1)}")
            }
        }
        return if (out.isEmpty()) "No messages found." else out.joinToString("\n")
    }

    @SuppressLint("MissingPermission")
    fun placeCall(ctx: Context, number: String) {
        val tm = ctx.getSystemService(TelecomManager::class.java)
        tm.placeCall(Uri.fromParts("tel", number, null), null)
    }

    @SuppressLint("MissingPermission")
    fun answerCall(ctx: Context): Boolean = try {
        ctx.getSystemService(TelecomManager::class.java).acceptRingingCall(); true
    } catch (e: Exception) {
        false
    }

    @SuppressLint("MissingPermission")
    fun rejectCall(ctx: Context): Boolean = try {
        if (Build.VERSION.SDK_INT >= 28) {
            @Suppress("DEPRECATION")
            ctx.getSystemService(TelecomManager::class.java).endCall()
        } else false
    } catch (e: Exception) {
        false
    }

    fun battery(ctx: Context): Int =
        ctx.getSystemService(BatteryManager::class.java).getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)

    /** Speak a phone number digit by digit when there is no contact name. */
    fun spokenNumber(number: String?): String =
        if (number.isNullOrBlank()) "a private number"
        else "number " + number.filter { it.isDigit() }.toCharArray().joinToString(" ")
}
