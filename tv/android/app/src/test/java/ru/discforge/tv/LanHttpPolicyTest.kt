package ru.discforge.tv

import com.sun.net.httpserver.HttpServer
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.URL
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class LanHttpPolicyTest {
    @Test fun media3StreamingClientDisablesBothRedirectTypes() {
        val client = LanHttpPolicy.streamingClient()
        assertFalse(client.followRedirects)
        assertFalse(client.followSslRedirects)
    }

    @Test fun catalogAndPosterRequestsNeverFollowTokenBearingRedirects() {
        val hits = AtomicInteger()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/library") { ex ->
            ex.responseHeaders.add("Location", "/other")
            ex.sendResponseHeaders(302, -1)
            ex.close()
        }
        server.createContext("/other") { ex ->
            hits.incrementAndGet()
            ex.sendResponseHeaders(200, -1)
            ex.close()
        }
        server.start()
        try {
            val connection = URL("http://127.0.0.1:${server.address.port}/library")
                .openConnection() as HttpURLConnection
            LanHttpPolicy.configure(connection)
            connection.connectTimeout = 2000
            connection.readTimeout = 2000
            connection.setRequestProperty("X-DiscForge-Token", "dummy-test-token")
            try {
                assertEquals(302, connection.responseCode)
                assertFalse(connection.instanceFollowRedirects)
                assertEquals(0, hits.get())
            } finally {
                connection.disconnect()
            }
        } finally {
            server.stop(0)
        }
    }
}
