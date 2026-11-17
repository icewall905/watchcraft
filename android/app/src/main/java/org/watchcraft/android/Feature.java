package org.watchcraft.android;

/** Keep these destinations aligned with frontend/src/lib/navigation.ts. */
final class Feature {
    final String title, path, icon, group;
    /** False for pages reached inside another one (Meme Radar lives in The Commons) but still choosable as shortcuts. */
    final boolean menu;
    Feature(String title, String path, String icon, String group) { this(title, path, icon, group, true); }
    Feature(String title, String path, String icon, String group, boolean menu) {
        this.title = title; this.path = path; this.icon = icon; this.group = group; this.menu = menu;
    }
    static final Feature[] ALL = {
        new Feature("The Screen", "/", "tv", "Watch"),
        new Feature("The Scroll", "/shorts", "play", "Watch"),
        new Feature("The Jukebox", "/music", "music", "Watch"),
        new Feature("The Marquee", "/jellyfin", "screen", "Watch"),
        new Feature("Subscriptions", "/subscriptions", "list", "Watch"),
        new Feature("The Front Page", "/today", "today", "Explore"),
        new Feature("The Finds", "/discover", "compass", "Explore"),
        new Feature("The Wire", "/news", "news", "Explore"),
        new Feature("The Boards", "/boards", "library", "Explore"),
        new Feature("The Commons", "/common-room", "smile", "Explore"),
        new Feature("Meme Radar", "/memes", "smile", "Explore", false),
        new Feature("The Stacks", "/library", "library", "Explore"),
        new Feature("The Almanac", "/knowledge", "book", "Explore"),
        new Feature("The Dispatch", "/briefings", "list", "Explore"),
        new Feature("Alerts", "/alerts", "bell", "Explore"),
        new Feature("The Study", "/research", "search", "Research"),
        new Feature("The Scout", "/scout-report", "compass", "Research"),
        new Feature("Agent activity", "/team", "activity", "Workshop"),
        new Feature("Curators", "/curators", "spark", "Workshop"),
        new Feature("Sources", "/sources", "globe", "Workshop"),
        new Feature("Video AI", "/agent", "spark", "Workshop"),
        new Feature("Statistics", "/stats", "activity", "Workshop"),
        new Feature("Settings", "/settings", "settings", "Manage")
    };
    static String titleFor(String path) {
        if (path.startsWith("/watch/")) return "Now playing";
        if (path.startsWith("/item/") || path.startsWith("/news/")) return "Reading";
        if (path.startsWith("/search") || path.startsWith("/find")) return "Search";
        if (path.startsWith("/category/") || path.startsWith("/channel/")) return "The Screen";
        if (path.equals("/tv")) return "The Screen";
        for (Feature f : ALL) if (path.equals(f.path) || (!f.path.equals("/") && path.startsWith(f.path + "/"))) return f.title;
        return "The Hub";
    }
}
