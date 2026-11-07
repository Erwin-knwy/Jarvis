package com.jarvis.phone

import java.util.Locale

/** Understands the short spoken answers Jarvis waits for. */
object Commands {
    enum class CallAction { ANSWER, REJECT, REJECT_AND_TEXT, NONE }
    enum class YesNo { YES, NO, UNKNOWN }

    private fun words(s: String) = s.lowercase(Locale.ROOT).replace(Regex("[^a-z ]"), " ").split(" ").filter { it.isNotBlank() }.toSet()

    fun callAction(heard: List<String>): CallAction {
        for (h in heard) {
            val w = words(h)
            val text = h.lowercase(Locale.ROOT)
            val textBack = ("text" in w || "message" in w || "sms" in w || "busy" in w)
            when {
                textBack && ("reject" in w || "decline" in w || "no" in w || "busy" in w || "text" in w || "message" in w) ->
                    return CallAction.REJECT_AND_TEXT
                "answer" in w || "accept" in w || "pick" in w || "yes" in w || "take" in w || "sure" in w ||
                    text.contains("put her through") || text.contains("put him through") || text.contains("put it through") ->
                    return CallAction.ANSWER
                "reject" in w || "decline" in w || "no" in w || "ignore" in w || "hang" in w || "deny" in w || "cancel" in w ->
                    return CallAction.REJECT
            }
        }
        return CallAction.NONE
    }

    fun yesNo(heard: List<String>): YesNo {
        for (h in heard) {
            val w = words(h)
            if (w.any { it in setOf("yes", "yeah", "yep", "sure", "send", "reply", "ok", "okay", "correct", "please", "affirmative") }) return YesNo.YES
            if (w.any { it in setOf("no", "nope", "cancel", "don't", "dont", "stop", "nevermind", "never", "ignore") }) return YesNo.NO
        }
        return YesNo.UNKNOWN
    }

    /** "reply saying I'm on my way" / "tell her I'm on my way" → "I'm on my way" (null if no inline text). */
    fun inlineReply(heard: List<String>): String? {
        val h = heard.firstOrNull() ?: return null
        val m = Regex("^(?:yes[, ]*)?(?:reply|answer|text|tell (?:her|him|them))(?: back)?(?: saying| that|:)?\\s+(.{2,})$", RegexOption.IGNORE_CASE)
            .find(h.trim()) ?: return null
        return m.groupValues[1].trim().replaceFirstChar { it.uppercase() }
    }
}
