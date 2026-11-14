package org.watchcraft.android;

/** Keep these destinations aligned with frontend/src/lib/navigation.ts. */
final class Feature {
    final String title, path, icon, group;
    Feature(String title, String path, String icon, String group) {
        this.title = title; this.path = path; this.icon = icon; this.group = group;
    }
    static final Feature[] ALL = {
        new Feature("TV", "/", "tv", "Watch"),
        new Feature("Feeds", "/shorts", "play", "Watch"),
        new Feature("Music videos", "/music", "music", "Watch"),
        new Feature("Subscriptions", "/subscriptions", "list", "Watch"),
        new Feature("Today", "/today", "today", "Explore"),
        new Feature("Discover", "/discover", "compass", "Explore"),
        new Feature("News", "/news", "news", "Explore"),
        new Feature("Meme Tracker", "/memes", "smile", "Explore"),
        new Feature("Library", "/library", "library", "Explore"),
        new Feature("Knowledge", "/knowledge", "book", "Explore"),
        new Feature("Briefings", "/briefings", "list", "Explore"),
        new Feature("Alerts", "/alerts", "bell", "Explore"),
        new Feature("Research", "/research", "search", "Research"),
        new Feature("Scout Report", "/scout-report", "compass", "Research"),
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
        if (path.startsWith("/category/") || path.startsWith("/channel/")) return "TV";
        if (path.equals("/tv")) return "TV";
        for (Feature f : ALL) if (path.equals(f.path) || (!f.path.equals("/") && path.startsWith(f.path + "/"))) return f.title;
        return "WatchCraft";
    }
}
