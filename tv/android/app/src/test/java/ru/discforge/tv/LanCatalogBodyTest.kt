package ru.discforge.tv

import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LanCatalogBodyTest {
    @Test fun readsUnicodeJson() {
        val content = """{"items":[{"title":"Фильм 🎬"}]}"""
        assertEquals(content, LanCatalogBody.read(ByteArrayInputStream(content.toByteArray())))
    }

    @Test fun exactlyEightMebibytesIsAccepted() {
        val data = ByteArray(LanCatalogBody.MAX_BYTES) { 'x'.code.toByte() }
        assertEquals(data.size, LanCatalogBody.read(ByteArrayInputStream(data)).length)
    }

    @Test fun rejectsAdvertisedOversizedCatalogWithoutReadingStream() {
        var touched = false
        val input = object : InputStream() {
            override fun read(): Int { touched = true; throw AssertionError("Stream read unexpectedly") }
        }
        expectIOException("8 МиБ") {
            LanCatalogBody.read(input, LanCatalogBody.MAX_BYTES.toLong() + 1L)
        }
        assertEquals(false, touched)
    }

    @Test fun rejectsChunkedOversizedCatalogWithoutReadingMegabytesExtra() {
        val payload = ByteArray(LanCatalogBody.MAX_BYTES + 65536) { 42 }
        var readBytes = 0
        val input = object : ByteArrayInputStream(payload) {
            override fun read(buffer: ByteArray, off: Int, len: Int): Int {
                val count = super.read(buffer, off, len)
                if (count > 0) readBytes += count
                return count
            }
        }
        expectIOException("8 МиБ") { LanCatalogBody.read(input) }
        assertEquals(LanCatalogBody.MAX_BYTES + 1, readBytes)
    }

    @Test fun rejectsLieInContentLength() {
        val data = ByteArray(LanCatalogBody.MAX_BYTES + 1)
        expectIOException("8 МиБ") { LanCatalogBody.read(ByteArrayInputStream(data), 5) }
    }

    @Test fun rejectsMalformedUtf8() {
        expectIOException("UTF-8") {
            LanCatalogBody.read(ByteArrayInputStream(byteArrayOf(0xc3.toByte(), 0x28)))
        }
    }

    @Test fun propagatesNetworkIoFailure() {
        val input = object : InputStream() {
            override fun read(): Int = throw IOException("connection dropped")
            override fun read(buffer: ByteArray, off: Int, len: Int): Int = read()
        }
        expectIOException("connection dropped") { LanCatalogBody.read(input) }
    }

    @Test fun emptyCatalogPayloadReturnsEmptyStringForCallerToReject() {
        assertEquals("", LanCatalogBody.read(ByteArrayInputStream(ByteArray(0))))
    }

    private fun expectIOException(needle: String, block: () -> Unit) {
        try {
            block()
            throw AssertionError("Expected IOException")
        } catch (failure: IOException) {
            assertTrue("Message: ${failure.message}", failure.message!!.contains(needle))
        }
    }
}
