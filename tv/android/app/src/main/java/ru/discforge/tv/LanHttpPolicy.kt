package ru.discforge.tv

import java.net.HttpURLConnection
import okhttp3.OkHttpClient

/** Reject redirects rather than forwarding the LAN pairing token to another origin. */
internal object LanHttpPolicy {
    fun configure(connection: HttpURLConnection) {
        connection.instanceFollowRedirects = false
    }

    // Media3's default HTTP datasource follows same-scheme redirects.
    // OkHttp with both redirects disabled keeps auth credentials at the paired origin.
    fun streamingClient(): OkHttpClient = OkHttpClient.Builder()
        .followRedirects(false)
        .followSslRedirects(false)
        .build()
}
