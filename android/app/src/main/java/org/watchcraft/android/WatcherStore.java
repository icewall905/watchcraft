package org.watchcraft.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import java.security.KeyStore;
import java.nio.charset.StandardCharsets;

/** Encrypt the notification capability with the non-exportable Android Keystore key. */
public final class WatcherStore {
    private static final String KEY = "watchcraft-watcher-ntfy";
    public static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("watcher_background", Context.MODE_PRIVATE);
    }
    private static SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (store.containsAlias(KEY)) return (SecretKey) store.getKey(KEY, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }
    public static void save(Context context, WatcherConnection connection) throws Exception {
        JSONObject json = new JSONObject().put("server", connection.server()).put("endpoint", connection.endpoint())
                .put("since", connection.since());
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key());
        String encrypted = Base64.encodeToString(cipher.doFinal(json.toString().getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
        if (!prefs(context).edit().clear().putString("config", encrypted)
                .putBoolean("enabled", true).putString("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP)).commit()) {
            throw new java.io.IOException("Could not save background connection");
        }
    }
    public static WatcherConnection load(Context context) {
        SharedPreferences prefs = prefs(context);
        if (!prefs.contains("config")) return null;
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(prefs.getString("iv", ""), Base64.DEFAULT)));
            JSONObject json = new JSONObject(new String(cipher.doFinal(Base64.decode(prefs.getString("config", ""), Base64.DEFAULT)), StandardCharsets.UTF_8));
            return new WatcherConnection(json.getString("server"), json.getString("endpoint"), json.getLong("since"));
        } catch (Exception e) { return null; }
    }
    public static boolean enabled(Context context) { return prefs(context).getBoolean("enabled", false); }
    public static void disable(Context context) {
        prefs(context).edit().clear().commit();
        context.stopService(new android.content.Intent(context, WatcherMessaging.class));
        context.getSystemService(android.app.NotificationManager.class).cancel(2);
    }
}
