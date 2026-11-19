package org.watchcraft.android;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

/** The phone receives a generic notification. The authenticated Hub supplies the details. */
public final class WatcherMessaging extends FirebaseMessagingService {
    static final String OPEN = "org.watchcraft.android.OPEN_WATCHER";
    private static final String CHANNEL = "watcher";

    @SuppressLint("MissingPermission")
    @Override public void onMessageReceived(RemoteMessage message) {
        if (!message.getData().containsKey("watcher_update")) return;
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "The Watcher", NotificationManager.IMPORTANCE_DEFAULT));
        Intent open = new Intent(this, MainActivity.class).setAction(OPEN)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent tap = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        manager.notify(CHANNEL, 1, new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_popup_reminder)
                .setContentTitle("The Watcher")
                .setContentText("You have a new Watcher update.")
                .setContentIntent(tap).setAutoCancel(true)
                .setVisibility(Notification.VISIBILITY_PRIVATE).build());
    }
}
