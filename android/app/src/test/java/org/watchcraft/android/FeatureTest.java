package org.watchcraft.android;

import java.util.HashSet;
import org.junit.Test;
import static org.junit.Assert.*;

public class FeatureTest {
    @Test public void allDestinationsAreUniqueAndLocal() {
        HashSet<String> paths = new HashSet<>();
        for (Feature f : Feature.ALL) { assertTrue(f.path.startsWith("/")); assertFalse(f.path.startsWith("//")); assertTrue(paths.add(f.path)); }
        assertTrue(paths.contains("/news"));
        assertTrue(paths.contains("/today"));
    }
    @Test public void detailPagesRetainFeatureTitles() {
        assertEquals("Reading", Feature.titleFor("/item/12"));
        assertEquals("Today", Feature.titleFor("/today/set-aside"));
        assertEquals("News", Feature.titleFor("/news"));
        assertEquals("Now playing", Feature.titleFor("/watch/abc"));
        assertEquals("Curators", Feature.titleFor("/curators/7"));
        assertEquals("Mood Board", Feature.titleFor("/boards"));
        assertEquals("TV", Feature.titleFor("/category/2"));
    }
}
