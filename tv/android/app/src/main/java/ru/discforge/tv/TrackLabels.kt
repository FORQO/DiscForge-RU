package ru.discforge.tv

import java.util.Locale

/** Defensive labels for untrusted metadata embedded in media files. */
internal object TrackLabels {
    private val languageTag = Regex("^[A-Za-z0-9]{2,8}(-[A-Za-z0-9]{1,8})*$")
    fun describe(label: String?, language: String?, channels: Int): String {
        val custom = label?.trim()?.takeIf { it.isNotEmpty() }
        val lang = language?.trim()?.takeIf {
            it.length <= 35 && languageTag.matches(it) && it != "und" && it != "zxx"
        }
        val fallback = lang?.let {
            runCatching { Locale.forLanguageTag(it).getDisplayLanguage(Locale.forLanguageTag("ru")) }
                .getOrNull()?.takeIf { it.isNotBlank() }
        }
        val name = (custom ?: fallback ?: "Неизвестная дорожка")
            .replace(Regex("[\\p{Cntrl}\\u202A-\\u202E\\u2066-\\u2069]"), " ")
            .take(64)
            .trim()
            .ifBlank { "Неизвестная дорожка" }
        val suffix = if (channels in 1..16) " · $channels кан." else ""
        return name + suffix
    }
}
