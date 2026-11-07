package org.watchcraft.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class AcceptTypesTest {
    private static final AcceptTypes.Lookup LOOKUP = extension -> {
        if (extension.equals("txt")) return "text/plain";
        if (extension.equals("json")) return "application/json";
        return null;
    };

    @Test public void extensionsBecomeMimeTypes() {
        assertArrayEquals(new String[]{"application/json"}, AcceptTypes.mimeTypes(new String[]{".json"}, LOOKUP));
        assertArrayEquals(new String[]{"text/plain"}, AcceptTypes.mimeTypes(new String[]{".TXT"}, LOOKUP));
    }
    @Test public void mimeTypesPassThrough() {
        assertArrayEquals(new String[]{"text/plain"}, AcceptTypes.mimeTypes(new String[]{"text/plain"}, LOOKUP));
        assertArrayEquals(new String[]{"image/*"}, AcceptTypes.mimeTypes(new String[]{"image/*"}, LOOKUP));
    }
    @Test public void mixedListsAreDeduplicated() {
        assertArrayEquals(new String[]{"text/plain"}, AcceptTypes.mimeTypes(new String[]{".txt", "text/plain"}, LOOKUP));
    }
    @Test public void unusableEntriesAreDropped() {
        assertArrayEquals(new String[0], AcceptTypes.mimeTypes(new String[]{".unknownext"}, LOOKUP));
        assertArrayEquals(new String[0], AcceptTypes.mimeTypes(new String[]{"", "   ", "plain"}, LOOKUP));
        assertArrayEquals(new String[0], AcceptTypes.mimeTypes(null, LOOKUP));
    }
}
