package org.watchcraft.android;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Converts WebView accept types into MIME types for the document picker.
 * Pages may list file extensions such as ".txt" instead of MIME types. Passing an
 * extension through as a MIME filter matches no document, so the picker shows an
 * empty folder and the page's file input becomes unusable.
 */
final class AcceptTypes {
    private AcceptTypes() {}

    /** Resolves a lowercase extension without the leading dot, or null when unknown. */
    interface Lookup { String forExtension(String extension); }

    static String[] mimeTypes(String[] acceptTypes, Lookup lookup) {
        List<String> types = new ArrayList<>();
        if (acceptTypes == null) return new String[0];
        for (String accept : acceptTypes) {
            if (accept == null) continue;
            String value = accept.trim();
            if (value.isEmpty()) continue;
            if (value.startsWith(".")) {
                String mime = lookup.forExtension(value.substring(1).toLowerCase(Locale.ROOT));
                if (mime != null && !mime.isEmpty() && !types.contains(mime)) types.add(mime);
            } else if (value.contains("/") && !types.contains(value)) {
                types.add(value);
            }
        }
        return types.toArray(new String[0]);
    }
}
