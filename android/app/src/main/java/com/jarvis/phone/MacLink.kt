package com.jarvis.phone

import android.annotation.SuppressLint
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import java.security.KeyStore

/** HTTP client that trusts either normal certificates or exactly the pinned Mac certificate. */
object Net {
    fun client(pin: String, readTimeoutSec: Long = 15): OkHttpClient {
        val b = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(readTimeoutSec, TimeUnit.SECONDS)
            .pingInterval(25, TimeUnit.SECONDS)
        if (pin.isNotBlank()) {
            val tm = PinnedTrustManager(pin)
            val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }
            b.sslSocketFactory(ssl.socketFactory, tm)
            // The certificate itself is pinned, so the host name check adds nothing (IPs change on Wi-Fi).
            b.hostnameVerifier { _, _ -> true }
        }
        return b.build()
    }

    @SuppressLint("CustomX509TrustManager")
    private class PinnedTrustManager(private val pin: String) : X509TrustManager {
        private val system: X509TrustManager = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            .apply { init(null as KeyStore?) }.trustManagers.filterIsInstance<X509TrustManager>().first()

        override fun checkClientTrusted(chain: Array<out X509Certificate>, authType: String) = throw CertificateException()
        override fun checkServerTrusted(chain: Array<out X509Certificate>, authType: String) {
            val leaf = chain.firstOrNull() ?: throw CertificateException("empty chain")
            val hash = MessageDigest.getInstance("SHA-256").digest(leaf.encoded).joinToString("") { "%02x".format(it) }
            if (hash == pin) return
            system.checkServerTrusted(chain, authType) // e.g. a real Tailscale certificate
        }
        override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
    }
}

/**
 * Persistent WebSocket to the Mac (/phone). Executes commands the Mac sends
 * (send_sms, call, recent_sms, status) and forwards phone events to it.
 */
class MacLink(
    private val prefs: Prefs,
    private val onCommand: (JSONObject) -> JSONObject,
    private val onState: (Boolean) -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private var ws: WebSocket? = null
    private var stopped = true
    private var backoffMs = 2000L
    @Volatile var connected = false
        private set

    fun start() {
        stopped = false
        connect()
    }

    fun stop() {
        stopped = true
        ws?.close(1000, "bye")
        ws = null
    }

    fun restart() {
        stop(); start()
    }

    fun sendEvent(event: String, extra: Map<String, String>) {
        val o = JSONObject().put("type", "event").put("event", event)
        extra.forEach { (k, v) -> o.put(k, v) }
        ws?.send(o.toString())
    }

    private fun connect() {
        if (stopped || !prefs.linked) return
        val base = prefs.serverUrl.replaceFirst("https://", "wss://").replaceFirst("http://", "ws://")
        val url = "$base/phone?token=${URLEncoder.encode(prefs.token, "UTF-8")}&device=${URLEncoder.encode(android.os.Build.MODEL, "UTF-8")}"
        val req = try { Request.Builder().url(url).build() } catch (e: IllegalArgumentException) { return }
        ws = Net.client(prefs.certPin, readTimeoutSec = 0).newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                connected = true; backoffMs = 2000L; main.post { onState(true) }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val msg = try { JSONObject(text) } catch (e: Exception) { return }
                if (!msg.has("id") || !msg.has("cmd")) return
                // Run on the main thread: contacts/SMS/telecom calls are quick and some need a Looper.
                main.post {
                    val reply = try {
                        onCommand(msg)
                    } catch (e: Exception) {
                        JSONObject().put("ok", false).put("error", e.message ?: e.javaClass.simpleName)
                    }
                    reply.put("id", msg.getString("id"))
                    webSocket.send(reply.toString())
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(code)
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w("JarvisLink", "link failed: ${t.message}")
                lost(response?.code ?: 0)
            }
        })
    }

    private fun lost(code: Int) {
        connected = false
        main.post { onState(false) }
        if (stopped || code == 4401) return
        main.postDelayed({ connect() }, backoffMs)
        backoffMs = (backoffMs * 2).coerceAtMost(60_000L)
    }
}
