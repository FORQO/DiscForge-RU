package ru.discforge.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TrackLabelsTest {
    @Test fun customNamesAndChannels() {
        assertEquals("Director's cut · 6 кан.", TrackLabels.describe("Director's cut", "en", 6))
    }

    @Test fun localizedLanguage() {
        assertTrue(TrackLabels.describe(null, "ru", 0).contains("рус"))
    }

    @Test fun unspecifiedLanguageFallback() {
        assertEquals("Неизвестная дорожка", TrackLabels.describe(null, "und", 0))
    }

    @Test fun absurdMediaLabelsAreBounded() {
        assertEquals(64, TrackLabels.describe("x".repeat(100), "en", 33).length)
    }
}
