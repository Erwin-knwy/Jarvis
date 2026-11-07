package com.jarvis.phone

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import okhttp3.Request
import java.io.File
import java.net.URLEncoder
import java.util.Locale
import kotlin.concurrent.thread

/**
 * Speaks with Jarvis's own voice (Piper on the Mac, via /api/tts) when the Mac is reachable,
 * otherwise with the phone's British TTS voice. Always call from the main thread.
 */
class Speaker(private val ctx: Context, private val prefs: Prefs) {
    private val main = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var player: MediaPlayer? = null
    private var generation = 0
    private val attrs = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANT)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()

    init {
        tts = TextToSpeech(ctx) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val t = tts ?: return@TextToSpeech
                t.language = Locale.UK
                t.setAudioAttributes(attrs)
                // Prefer an offline British male voice if the engine has one.
                t.voices?.filter { it.locale == Locale.UK && !it.isNetworkConnectionRequired }
                    ?.sortedBy { v -> listOf("gbd", "rjs", "gbc", "male").indexOfFirst { v.name.contains(it) }.let { if (it < 0) 99 else it } }
                    ?.firstOrNull()?.let { t.voice = it }
                t.setSpeechRate(0.95f)
                ttsReady = true
            }
        }
    }

    /** Speak, then call [done] (on the main thread) when finished or interrupted. */
    fun say(text: String, done: () -> Unit = {}) {
        stop()
        val gen = ++generation
        val finish: () -> Unit = { main.post { if (gen == generation) done() } }
        if (prefs.useMacVoice && prefs.linked) {
            thread(name = "jarvis-tts") {
                val file = fetchMacVoice(text)
                main.post {
                    if (gen != generation) return@post
                    if (file != null) playFile(file, text, gen, finish) else speakLocal(text, gen, finish)
                }
            }
        } else {
            speakLocal(text, gen, finish)
        }
    }

    fun stop() {
        generation++
        player?.let { try { it.stop() } catch (_: Exception) {}; it.release() }
        player = null
        tts?.stop()
    }

    fun shutdown() {
        stop(); tts?.shutdown()
    }

    private fun fetchMacVoice(text: String): File? = try {
        val url = "${prefs.serverUrl}/api/tts?text=${URLEncoder.encode(text, "UTF-8")}"
        val req = Request.Builder().url(url).header("Authorization", "Bearer ${prefs.token}").build()
        Net.client(prefs.certPin, readTimeoutSec = 6).newCall(req).execute().use { r ->
            if (!r.isSuccessful) null
            else File(ctx.cacheDir, "say.wav").apply { writeBytes(r.body!!.bytes()) }
        }
    } catch (e: Exception) {
        Log.i("JarvisVoice", "Mac voice unavailable: ${e.message}")
        null
    }

    private fun playFile(f: File, text: String, gen: Int, finish: () -> Unit) {
        try {
            player = MediaPlayer().apply {
                setAudioAttributes(attrs)
                setDataSource(f.absolutePath)
                setOnCompletionListener { finish() }
                setOnErrorListener { _, _, _ -> finish(); true }
                prepare()
                start()
            }
        } catch (e: Exception) {
            speakLocal(text, gen, finish)
        }
    }

    private fun speakLocal(text: String, gen: Int, finish: () -> Unit) {
        val t = tts
        if (t == null || !ttsReady) {
            // TTS still starting up: retry shortly.
            main.postDelayed({ if (gen == generation) { if (ttsReady) speakLocal(text, gen, finish) else finish() } }, 800)
            return
        }
        val id = "u$gen"
        t.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) { if (utteranceId == id) finish() }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) { if (utteranceId == id) finish() }
        })
        t.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle(), id)
    }
}

/** One-shot speech recognition ("answer", "reject", a dictated reply…). Main thread only. */
class Listener(private val ctx: Context) {
    private var recognizer: SpeechRecognizer? = null

    val available: Boolean get() = SpeechRecognizer.isRecognitionAvailable(ctx)

    /** Calls [onResult] once with the candidate transcripts (empty list = nothing heard / error). */
    fun listen(longForm: Boolean = false, onResult: (List<String>) -> Unit) {
        cancel()
        if (!available) { onResult(emptyList()); return }
        var delivered = false
        val deliver = { r: List<String> -> if (!delivered) { delivered = true; onResult(r) } }
        val rec = SpeechRecognizer.createSpeechRecognizer(ctx)
        recognizer = rec
        rec.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                deliver(results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION) ?: emptyList())
            }
            override fun onError(error: Int) {
                Log.i("JarvisListen", "recognizer error $error")
                deliver(emptyList())
            }
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, !longForm)
            if (longForm) {
                putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
            }
        }
        rec.startListening(intent)
    }

    fun cancel() {
        recognizer?.let { try { it.cancel(); it.destroy() } catch (_: Exception) {} }
        recognizer = null
    }
}
