package org.watchcraft.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class ServerAddressTest {
    @Test public void normalizesHostAndDefaultScheme() {
        assertEquals("https://wc.tdgs.org", ServerAddress.normalize("  wc.tdgs.org/  ", false));
        assertEquals("https://wc.tdgs.org", ServerAddress.normalize("HTTPS://WC.TDGS.ORG:443/", false));
        assertEquals("http://10.0.0.2:6645", ServerAddress.normalize("http://10.0.0.2:6645", true));
        assertEquals("http://[::1]:6645", ServerAddress.normalize("http://[::1]:6645", true));
    }
    @Test public void rejectsUnsafeAndUnsupportedAddresses() {
        for (String input : new String[]{"", "javascript:alert(1)", "ftp://wc.tdgs.org", "https://name:secret@wc.tdgs.org", "https://wc.tdgs.org/foo", "https://wc.tdgs.org?x=y", "https://wc.tdgs.org#hello", "https://wc.tdgs.org:0", "https://wc.tdgs.org:65536", "https://bad host", "https://wc.tdgs.org/%2e%2e"}) {
            try { ServerAddress.normalize(input, false); fail("Accepted: " + input); }
            catch (IllegalArgumentException expected) { /* required */ }
        }
    }
    @Test public void httpNeedsExplicitConsent() {
        try { ServerAddress.normalize("http://192.168.1.2", false); fail("HTTP allowed silently"); }
        catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("unencrypted")); }
    }
    @Test public void originDoesNotConfuseLookalikeHostsCredentialsOrPorts() {
        String server = "https://wc.tdgs.org";
        assertTrue(ServerAddress.sameOrigin(server, "https://wc.tdgs.org:443/api/health"));
        assertTrue(ServerAddress.sameOrigin(server, "https://WC.TDGS.ORG/today?q=test"));
        for (String other : new String[]{"http://wc.tdgs.org/", "https://wc.tdgs.org.evil.example", "https://wc.tdgs.org@evil.example", "https://evil@wc.tdgs.org/", "https://wc.tdgs.org:444/", "file:///etc/passwd", "javascript:alert(1)", "intent://wc.tdgs.org", "https://wc.tdgs.org\\@evil.example", "//wc.tdgs.org/"}) {
            assertFalse(other, ServerAddress.sameOrigin(server, other));
        }
        assertFalse(ServerAddress.sameOrigin(server, null));
    }
    @Test public void browserLinksExcludeLocalAndExecutableSchemes() {
        assertTrue(ServerAddress.isWebLink("https://example.org/article"));
        for (String url : new String[]{"file:///data/a", "content://contacts", "intent://open", "javascript:alert(1)", "data:text/html,hi", "https://user:pass@example.org"}) assertFalse(url, ServerAddress.isWebLink(url));
    }
}
