package org.watchcraft.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class ServerAddressTest {
    @Test public void normalizesHostAndDefaultScheme() {
        assertEquals("https://hub.tdgs.org", ServerAddress.normalize("  hub.tdgs.org/  ", false));
        assertEquals("https://hub.tdgs.org", ServerAddress.normalize("HTTPS://HUB.TDGS.ORG:443/", false));
        assertEquals("http://10.0.0.2:6645", ServerAddress.normalize("http://10.0.0.2:6645", true));
        assertEquals("http://[::1]:6645", ServerAddress.normalize("http://[::1]:6645", true));
    }
    @Test public void rejectsUnsafeAndUnsupportedAddresses() {
        for (String input : new String[]{"", "javascript:alert(1)", "ftp://hub.tdgs.org", "https://name:secret@hub.tdgs.org", "https://hub.tdgs.org/foo", "https://hub.tdgs.org?x=y", "https://hub.tdgs.org#hello", "https://hub.tdgs.org:0", "https://hub.tdgs.org:65536", "https://bad host", "https://hub.tdgs.org/%2e%2e"}) {
            try { ServerAddress.normalize(input, false); fail("Accepted: " + input); }
            catch (IllegalArgumentException expected) { /* required */ }
        }
    }
    @Test public void httpNeedsExplicitConsent() {
        try { ServerAddress.normalize("http://192.168.1.2", false); fail("HTTP allowed silently"); }
        catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("unencrypted")); }
    }
    @Test public void originDoesNotConfuseLookalikeHostsCredentialsOrPorts() {
        String server = "https://hub.tdgs.org";
        assertTrue(ServerAddress.sameOrigin(server, "https://hub.tdgs.org:443/api/health"));
        assertTrue(ServerAddress.sameOrigin(server, "https://HUB.TDGS.ORG/today?q=test"));
        for (String other : new String[]{"http://hub.tdgs.org/", "https://hub.tdgs.org.evil.example", "https://hub.tdgs.org@evil.example", "https://evil@hub.tdgs.org/", "https://hub.tdgs.org:444/", "file:///etc/passwd", "javascript:alert(1)", "intent://hub.tdgs.org", "https://hub.tdgs.org\\@evil.example", "//hub.tdgs.org/"}) {
            assertFalse(other, ServerAddress.sameOrigin(server, other));
        }
        assertFalse(ServerAddress.sameOrigin(server, null));
    }
    @Test public void browserLinksExcludeLocalAndExecutableSchemes() {
        assertTrue(ServerAddress.isWebLink("https://example.org/article"));
        for (String url : new String[]{"file:///data/a", "content://contacts", "intent://open", "javascript:alert(1)", "data:text/html,hi", "https://user:pass@example.org"}) assertFalse(url, ServerAddress.isWebLink(url));
    }

    @Test
    public void migratesRenamedHouseholdServer() {
        assertEquals("https://hub.tdgs.org", ServerAddress.migrate("https://wc.tdgs.org"));
        assertEquals("https://example.org", ServerAddress.migrate("https://example.org"));
    }
}
