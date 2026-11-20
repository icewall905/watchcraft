package org.watchcraft.android;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Build;
import android.os.IBinder;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashSet;
import java.util.Set;

/** Persistent ntfy JSON stream, independent of the WebView. */
public class WatcherMessaging extends Service {
    public static final String STATUS_CHANNEL = "watcher_connection";
    public static final String MESSAGE_CHANNEL = "watcher_updates";
    static final String OPEN = "org.watchcraft.android.OPEN_WATCHER";
    private volatile boolean running;
    private volatile HttpURLConnection connection;
    private volatile WatcherConnection listening;
    private Thread worker;
    private ConnectivityManager connectivity;
    private final Object retryLock = new Object();
    private ConnectivityManager.NetworkCallback networkCallback;

    @Override public IBinder onBind(Intent intent) { return null; }
    public static boolean permitted(android.content.Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        NotificationChannel channel = manager.getNotificationChannel(MESSAGE_CHANNEL);
        return (Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
                && manager.areNotificationsEnabled() && (channel == null || channel.getImportance() != NotificationManager.IMPORTANCE_NONE);
    }
    public static void start(android.content.Context context) {
        if (WatcherStore.enabled(context) && permitted(context)) {
            try { context.startForegroundService(new Intent(context, WatcherMessaging.class)); }
            catch (RuntimeException ignored) { /* Restricted by Android: reopening the app retries. */ }
        }
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        WatcherConnection saved = WatcherStore.load(this);
        if (!WatcherStore.enabled(this) || !permitted(this) || saved == null) {
            stopSelf(); return START_NOT_STICKY;
        }
        if (running && saved.equals(listening)) return START_STICKY;
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(STATUS_CHANNEL, "Background connection", NotificationManager.IMPORTANCE_LOW));
        NotificationChannel messages = new NotificationChannel(MESSAGE_CHANNEL, "Watcher updates", NotificationManager.IMPORTANCE_HIGH);
        messages.setDescription("Generic alerts for Watcher updates");
        messages.enableVibration(true); messages.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        manager.createNotificationChannel(messages);
        Notification status = status("Connecting for Watcher updates…");
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, status, ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING);
        else startForeground(1, status);
        if (running) {
            HttpURLConnection current = connection;
            if (current != null) current.disconnect();
            synchronized (retryLock) { retryLock.notifyAll(); }
            return START_STICKY;
        }
        running = true;
        connectivity = getSystemService(ConnectivityManager.class);
        networkCallback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) {
                HttpURLConnection current = connection;
                if (current != null) current.disconnect();
                synchronized (retryLock) { retryLock.notifyAll(); }
            }
        };
        connectivity.registerDefaultNetworkCallback(networkCallback);
        worker = new Thread(this::listen, "watcher-background"); worker.start();
        return START_STICKY;
    }
    private Notification status(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(this, 1, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, STATUS_CHANNEL).setSmallIcon(android.R.drawable.ic_popup_reminder)
                .setContentTitle("The Watcher").setContentText(text).setContentIntent(pending)
                .setOngoing(true).setOnlyAlertOnce(true).setShowWhen(false).build();
    }
    @android.annotation.SuppressLint("MissingPermission")
    private void updateStatus(String text) {
        if (!permitted(this)) return;
        getSystemService(NotificationManager.class).notify(1, status(text));
    }
    private void listen() {
        int delay = 2;
        while (running && WatcherStore.enabled(this)) {
            WatcherConnection config = WatcherStore.load(this);
            if (config == null) break;
            listening = config;
            SharedPreferences prefs = WatcherStore.prefs(this);
            String cursor = prefs.getString("cursor", Long.toString(config.since()));
            try {
                connection = (HttpURLConnection) new URL(config.endpoint() + "/json?since=" + cursor).openConnection();
                connection.setConnectTimeout(15000); connection.setReadTimeout(90000);
                connection.setInstanceFollowRedirects(false);
                int status = connection.getResponseCode();
                if (status == 400) {
                    // Server cache may have expired the last ID after a long outage.
                    prefs.edit().putString("cursor", "24h").commit();
                    throw new IOException("Expired cursor");
                }
                if (status != 200) throw new IOException("Connection refused");
                updateStatus("Listening for Watcher updates"); delay = 2;
                try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while (running && (line = boundedLine(reader)) != null) {
                        if (!line.isEmpty()) receive(new JSONObject(line), config, prefs);
                    }
                }
            } catch (Exception ignored) {
                // Never log the private topic or its URL.
            } finally {
                HttpURLConnection current = connection; connection = null;
                if (current != null) current.disconnect();
            }
            if (!running) break;
            updateStatus("Reconnecting — updates will catch up when online");
            synchronized (retryLock) {
                try { retryLock.wait(delay * 1000L); } catch (InterruptedException e) { break; }
            }
            delay = Math.min(60, delay * 2);
        }
    }
    static String boundedLine(BufferedReader reader) throws IOException {
        StringBuilder line = new StringBuilder(); int character;
        while ((character = reader.read()) != -1) {
            if (character == '\n') return line.toString();
            if (line.length() >= 16384) throw new IOException("Oversized event");
            if (character != '\r') line.append((char) character);
        }
        return line.length() == 0 ? null : line.toString();
    }
    @android.annotation.SuppressLint("MissingPermission")
    void receive(JSONObject event, WatcherConnection config, SharedPreferences prefs) throws Exception {
        if (!running || !config.equals(WatcherStore.load(this))) return;
        if (!"message".equals(event.optString("event"))) return;
        String id = event.optString("id");
        if (!id.matches("[a-zA-Z0-9_-]{1,64}")) return;
        String dedupe = id;
        JSONArray tags = event.optJSONArray("tags");
        if (tags != null && tags.length() > 0 && tags.optString(0).matches("watcher-[0-9]{1,20}")) dedupe = tags.getString(0);
        Set<String> recent = new LinkedHashSet<>();
        JSONArray stored = new JSONArray(prefs.getString("recent", "[]"));
        for (int i = 0; i < stored.length(); i++) recent.add(stored.getString(i));
        if (dedupe.startsWith("watcher-") && !recent.contains(dedupe) && WatcherStore.enabled(this) && permitted(this)) {
            Intent open = new Intent(this, MainActivity.class).setAction(OPEN)
                    .putExtra("notification_server", config.server())
                    .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            PendingIntent pending = PendingIntent.getActivity(this, 2, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            Notification message = new Notification.Builder(this, MESSAGE_CHANNEL).setSmallIcon(android.R.drawable.ic_popup_reminder)
                    .setContentTitle("The Watcher").setContentText("You have a new Watcher update.")
                    .setVisibility(Notification.VISIBILITY_PRIVATE).setContentIntent(pending).setAutoCancel(true)
                    .setCategory(Notification.CATEGORY_MESSAGE).build();
            // One replacement notification avoids revealing message counts or flooding after reconnect.
            getSystemService(NotificationManager.class).notify(2, message);
        }
        if (!config.equals(WatcherStore.load(this))) return;
        recent.add(dedupe);
        while (recent.size() > 256) recent.remove(recent.iterator().next());
        prefs.edit().putString("cursor", id).putString("recent", new JSONArray(recent).toString()).commit();
    }
    @Override public void onDestroy() {
        running = false;
        if (connection != null) connection.disconnect();
        if (worker != null) worker.interrupt();
        synchronized (retryLock) { retryLock.notifyAll(); }
        if (connectivity != null && networkCallback != null) connectivity.unregisterNetworkCallback(networkCallback);
        super.onDestroy();
    }
}
