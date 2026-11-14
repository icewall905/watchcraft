package org.watchcraft.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class NavigationTest {
    @Test public void invalidAndDuplicateSavedChoicesRecoverWithoutExternalRoutes() {
        assertArrayEquals(Navigation.DEFAULTS, Navigation.restore(null));
        assertArrayEquals(new String[]{"/library", "/", "/today", "/news"},
                Navigation.restore(new String[]{"/library", "/library", "https://example.org"}));
        assertArrayEquals(new String[]{"/", "/today", "/news", "/memes"},
                Navigation.restore(new String[]{"/", "/alerts", "//example.org"}));
    }
    @Test public void selectingExistingShortcutSwapsRatherThanDuplicating() {
        String[] paths = Navigation.DEFAULTS.clone();
        Navigation.replace(paths, 0, "/memes");
        assertArrayEquals(new String[]{"/memes", "/today", "/news", "/"}, paths);
        Navigation.replace(paths, 1, "/library");
        assertArrayEquals(new String[]{"/memes", "/library", "/news", "/"}, paths);
        Navigation.replace(paths, 1, "/alerts");
        assertEquals("/library", paths[1]);
    }
    @Test public void moodBoardCanBeChosenAsAShortcut() {
        assertEquals("Mood Board", Navigation.feature("/boards").title);
        String[] paths = Navigation.DEFAULTS.clone();
        Navigation.replace(paths, 2, "/boards");
        assertEquals("/boards", Navigation.restore(paths)[2]);
    }
    @Test public void fourthAndFirstButtonsAreReplaceableAndTodayAndNewsAreSeparate() {
        String[] paths = Navigation.DEFAULTS.clone();
        Navigation.replace(paths, 3, "/library");
        Navigation.replace(paths, 0, "/music");
        assertArrayEquals(new String[]{"/music", "/today", "/news", "/library"}, paths);
        // Saved 0.1.8/0.1.9 choices (News merged) stay as they were; a saved Today is kept, not rewritten.
        assertArrayEquals(new String[]{"/", "/news", "/memes", "/boards"}, Navigation.restore(new String[]{"/", "/news", "/memes", "/boards"}));
        assertArrayEquals(new String[]{"/", "/today", "/news", "/memes"}, Navigation.restore(new String[]{"/", "/today", "/news", "/memes"}));
        assertFalse(Navigation.matches("/today", "/news"));
        assertNotNull(Navigation.feature("/"));
    }
    @Test public void nestedPagesSelectTheirOwnShortcutWithoutPrefixCollisions() {
        assertTrue(Navigation.matches("/news", "/news/12"));
        assertFalse(Navigation.matches("/news", "/newsletter"));
        assertFalse(Navigation.matches("/", "/news"));
    }
}
