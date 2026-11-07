package org.watchcraft.android;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** Pure URL policy shared by onboarding, WebView navigation, and permission checks. */
final class ServerAddress {
    private ServerAddress() {}

    static String normalize(String input, boolean allowHttp) {
        String value = input.trim();
        if (value.isEmpty()) throw new IllegalArgumentException("Enter your WatchCraft server address.");
        if (!value.contains("://")) value = "https://" + value;
        URI uri = parse(value);
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!(scheme.equals("https") || scheme.equals("http")))
            throw new IllegalArgumentException("Use an https:// or http:// server address.");
        if (scheme.equals("http") && !allowHttp)
            throw new IllegalArgumentException("For an HTTP server, enable the unencrypted connection option below.");
        if (uri.getHost() == null || uri.getHost().isEmpty() || uri.getRawUserInfo() != null)
            throw new IllegalArgumentException("Enter a server name without a username or password.");
        if (uri.getPort() == 0 || uri.getPort() > 65535 || uri.getPort() < -1)
            throw new IllegalArgumentException("The server port must be between 1 and 65535.");
        if (uri.getRawQuery() != null || uri.getRawFragment() != null ||
                !(uri.getRawPath() == null || uri.getRawPath().isEmpty() || uri.getRawPath().equals("/")))
            throw new IllegalArgumentException("Enter only the server address, for example https://wc.tdgs.org.");
        try {
            int port = uri.getPort();
            if (port == (scheme.equals("https") ? 443 : 80)) port = -1;
            return new URI(scheme, null, uri.getHost().toLowerCase(Locale.ROOT), port, null, null, null).toASCIIString();
        } catch (URISyntaxException e) {
            throw new IllegalArgumentException("That server address is not valid.");
        }
    }

    static boolean sameOrigin(String server, String target) {
        try {
            URI a = new URI(server), b = new URI(target);
            return a.getHost() != null && b.getHost() != null && b.getRawUserInfo() == null &&
                    a.getScheme().equalsIgnoreCase(b.getScheme()) && a.getHost().equalsIgnoreCase(b.getHost()) &&
                    port(a) == port(b);
        } catch (URISyntaxException | NullPointerException e) { return false; }
    }

    static boolean isWebLink(String target) {
        try {
            URI uri = new URI(target);
            return uri.getHost() != null && uri.getRawUserInfo() == null &&
                    ("https".equalsIgnoreCase(uri.getScheme()) || "http".equalsIgnoreCase(uri.getScheme()));
        } catch (URISyntaxException | NullPointerException e) { return false; }
    }

    private static int port(URI uri) {
        return uri.getPort() != -1 ? uri.getPort() : ("https".equalsIgnoreCase(uri.getScheme()) ? 443 : 80);
    }

    private static URI parse(String value) {
        try { return new URI(value); }
        catch (URISyntaxException e) { throw new IllegalArgumentException("That server address is not valid."); }
    }
}
