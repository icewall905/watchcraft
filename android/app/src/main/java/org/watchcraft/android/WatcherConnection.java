package org.watchcraft.android;

import java.net.URI;

record WatcherConnection(String server, String endpoint, long since) {
    WatcherConnection {
        server = ServerAddress.normalize(server, false);
        URI uri = URI.create(endpoint);
        if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null
                || uri.getQuery() != null || uri.getFragment() != null
                || !uri.getRawPath().matches("/wc_[a-f0-9]{60}") || since < 1) {
            throw new IllegalArgumentException("Invalid Watcher connection");
        }
    }
}
