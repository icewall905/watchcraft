package org.watchcraft.android;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class WatcherBootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())) {
            // Credential-encrypted config becomes available after the first unlock.
            WatcherMessaging.start(context);
        }
    }
}
