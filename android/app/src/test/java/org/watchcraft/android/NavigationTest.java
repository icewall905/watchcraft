package org.watchcraft.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class NavigationTest {
    @Test public void invalidAndDuplicateSavedChoicesRecoverWithoutExternalRoutes() {
        assertArrayEquals(Navigation.DEFAULTS, Navigation.restore(null));
        assertArrayEquals(new String[]{"/library", "/", "/news", "/memes"},
                Navigation.restore(new String[]{"/library", "/library", "https://example.org"}));
        assertArrayEquals(new String[]{"/", "/news", "/memes", "/boards"},
                Navigation.restore(new String[]{"/", "/alerts", "//example.org"}));
    }
    @Test public void selectingExistingShortcutSwapsRatherThanDuplicating() {
        String[] paths = Navigation.DEFAULTS.clone();
        Navigation.replace(paths, 0, "/memes");
        assertArrayEquals(new String[]{"/memes", "/news", "/", "/boards"}, paths);
        Navigation.replace(paths, 1, "/library");
        assertArrayEquals(new String[]{"/memes", "/library", "/", "/boards"}, paths);
        Navigation.replace(paths, 1, "/alerts");
        assertEquals("/library", paths[1]);
    }
    @Test public void moodBoardCanBeChosenAsAShortcut() {
        assertEquals("Mood Board", Navigation.feature("/boards").title);
        String[] paths = Navigation.DEFAULTS.clone();
        Navigation.replace(paths, 2, "/boards");
        assertEquals("/boards", Navigation.restore(paths)[2]);
    }
    @Test public void fourthAndFirstButtonsAreReplaceableAndLegacyTodayBecomesNews() {
        String[] paths = Navigation.DEFAULTS.clone();
        Navigation.replace(paths, 3, "/library");
        Navigation.replace(paths, 0, "/music");
        assertArrayEquals(new String[]{"/music", "/news", "/memes", "/library"}, paths);
        assertArrayEquals(new String[]{"/", "/news", "/boards", "/memes"}, Navigation.restore(new String[]{"/", "/today", "/news", "/memes"}));
        assertNotNull(Navigation.feature("/"));
    }
    @Test public void nestedPagesSelectTheirOwnShortcutWithoutPrefixCollisions() {
        assertTrue(Navigation.matches("/news", "/news/12"));
        assertFalse(Navigation.matches("/news", "/newsletter"));
        assertFalse(Navigation.matches("/", "/news"));
    }
}
