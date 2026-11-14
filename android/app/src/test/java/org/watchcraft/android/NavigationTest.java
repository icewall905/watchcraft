package org.watchcraft.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class NavigationTest {
    @Test public void invalidAndDuplicateSavedChoicesRecoverWithoutExternalRoutes() {
        assertArrayEquals(Navigation.DEFAULTS, Navigation.restore(null));
        assertArrayEquals(new String[]{"/library", "/today", "/news"},
                Navigation.restore(new String[]{"/library", "/library", "https://example.org"}));
        assertArrayEquals(new String[]{"/today", "/news", "/memes"},
                Navigation.restore(new String[]{"/", "/alerts", "//example.org"}));
    }
    @Test public void selectingExistingShortcutSwapsRatherThanDuplicating() {
        String[] paths = Navigation.DEFAULTS.clone();
        Navigation.replace(paths, 0, "/memes");
        assertArrayEquals(new String[]{"/memes", "/news", "/today"}, paths);
        Navigation.replace(paths, 1, "/library");
        assertArrayEquals(new String[]{"/memes", "/library", "/today"}, paths);
        Navigation.replace(paths, 1, "/alerts");
        assertEquals("/library", paths[1]);
    }
    @Test public void moodBoardCanBeChosenAsAShortcut() {
        assertEquals("Mood Board", Navigation.feature("/boards").title);
        String[] paths = Navigation.DEFAULTS.clone();
        Navigation.replace(paths, 2, "/boards");
        assertEquals("/boards", Navigation.restore(paths)[2]);
    }
    @Test public void nestedPagesSelectTheirOwnShortcutWithoutPrefixCollisions() {
        assertTrue(Navigation.matches("/news", "/news/12"));
        assertFalse(Navigation.matches("/news", "/newsletter"));
    }
}
