package ru.discforge.tv

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

/** Read the authorized LAN catalog without trusting Content-Length or chunked input. */
internal object LanCatalogBody {
    // Enough for thousands of film entries, but prevents unbounded TV heap use.
    const val MAX_BYTES = 8 * 1024 * 1024

    @Throws(IOException::class)
    fun read(input: InputStream, declaredLength: Long = -1L): String {
        if (declaredLength > MAX_BYTES) throw IOException("Каталог превышает 8 МиБ")
        val bytes = ByteArrayOutputStream()
        val chunk = ByteArray(8192)
        var received = 0
        while (true) {
            // Read at most one byte beyond the cap, even from chunked responses.
            val available = MAX_BYTES + 1 - received
            val count = input.read(chunk, 0, minOf(chunk.size, available))
            if (count < 0) break
            if (count == 0) {
                // Defensive handling for nonconforming InputStreams.
                val next = input.read()
                if (next < 0) break
                received++
                if (received > MAX_BYTES) throw IOException("Каталог превышает 8 МиБ")
                bytes.write(next)
                continue
            }
            received += count
            if (received > MAX_BYTES) throw IOException("Каталог превышает 8 МиБ")
            bytes.write(chunk, 0, count)
        }
        return try {
            Charsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes.toByteArray())).toString()
        } catch (exc: java.nio.charset.CharacterCodingException) {
            throw IOException("Каталог содержит некорректный UTF-8", exc)
        }
    }
}
