package org.watchcraft.android;

/** Device-local shortcut choices; only known destinations can be loaded from preferences. */
final class Navigation {
    static final String[] DEFAULTS = {"/", "/news", "/memes", "/boards"};
    static Feature[] choices() {
        return java.util.Arrays.stream(Feature.ALL)
                .filter(f -> !f.path.equals("/alerts"))
                .toArray(Feature[]::new);
    }
    static Feature feature(String path) {
        for (Feature f : choices()) if (f.path.equals(path)) return f;
        return null;
    }
    static String[] restore(String[] saved) {
        String[] result = new String[4];
        java.util.HashSet<String> used = new java.util.HashSet<>();
        for (int i = 0; i < 4; i++) {
            String path = saved != null && i < saved.length ? saved[i] : null;
            if ("/today".equals(path)) path = "/news";
            if (feature(path) != null && used.add(path)) result[i] = path;
        }
        for (int i = 0; i < 4; i++) if (result[i] == null) {
            for (String path : DEFAULTS) if (used.add(path)) { result[i] = path; break; }
        }
        return result;
    }
    static void replace(String[] paths, int slot, String path) {
        if (slot < 0 || slot >= 4 || feature(path) == null) return;
        for (int i = 0; i < 4; i++) if (paths[i].equals(path)) { paths[i] = paths[slot]; break; }
        paths[slot] = path;
    }
    static boolean matches(String destination, String path) {
        return path.equals(destination) || (!destination.equals("/") && path.startsWith(destination + "/"));
    }
}
