package org.watchcraft.android;

import android.Manifest;
import android.annotation.SuppressLint;
import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import android.app.AlertDialog;
import android.app.Dialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Message;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.MimeTypeMap;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.PopupMenu;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONObject;
import org.json.JSONTokener;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Native device shell. Feature pages remain served by the user's WatchCraft instance. */
public final class MainActivity extends ComponentActivity {
    private static final int BG = 0xff110e26, PANEL = 0xff1b1740, TEXT = 0xfff0edff,
            MUTED = 0xffaaa4d2, AMBER = 0xffffb547, LINE = 0xff383252, INK = 0xff211706;
    private static final int PICK_FILE = 41, RECORD_AUDIO = 42, EXPORT_FILE = 43, DOWNLOAD_FILE = 44;
    private static final int MAX_EXPORT_BYTES = 16 * 1024 * 1024;
    // No JavaScript interface is exposed. This CSS only removes duplicated web navigation.
    private static final String SHELL_CSS = ".sidebar,.bar[aria-label=Primary],.menu-toggle{display:none!important}"
            + ".desktop{margin-left:0!important;padding-bottom:0!important}"
            + "html{font-size:16px!important}body{overscroll-behavior-y:contain}"
            + "input,textarea,select{font-size:max(16px,1em)}";
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private SharedPreferences prefs;
    private String server = "", currentPath = "/";
    private LinearLayout root, shell, bottom;
    private FrameLayout content;
    private WebView web;
    private ProgressBar progress;
    private TextView heading;
    private View errorPanel, customView;
    private WebChromeClient.CustomViewCallback customCallback;
    private ValueCallback<Uri[]> fileCallback;
    private PermissionRequest microphoneRequest;
    private Dialog menu;
    private boolean loadingFailed, destroyed, hasConnected, requestingMicrophone, pageReady;
    private int connectionAttempt;
    private byte[] exportBytes;
    private String exportOrigin, pendingDownload, downloadOrigin, downloadCookie, downloadAgent;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("connection", MODE_PRIVATE);
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(BG);
        root.setFitsSystemWindows(false);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        getWindow().setStatusBarColor(BG); getWindow().setNavigationBarColor(BG);
        setContentView(root);
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() { goBack(); }
        });
        String saved = prefs.getString("server", "");
        if (!saved.isEmpty()) {
            try { server = ServerAddress.normalize(saved, prefs.getBoolean("http", false)); hasConnected = true; showService(savedInstanceState); }
            catch (IllegalArgumentException ignored) { showSetup(); }
        } else showSetup();
    }

    private void showSetup() {
        connectionAttempt++;
        denyMicrophone();
        if (fileCallback != null) { fileCallback.onReceiveValue(null); fileCallback = null; }
        if (web != null) { web.stopLoading(); web.onPause(); web.destroy(); web = null; }
        root.removeAllViews();
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true);
        LinearLayout outer = column(); outer.setGravity(Gravity.CENTER); outer.setPadding(dp(24), dp(24), dp(24), dp(24));
        LinearLayout form = column();
        int max = Math.min(getResources().getDisplayMetrics().widthPixels - dp(48), dp(460));
        outer.addView(form, new LinearLayout.LayoutParams(max, -2));
        ImageView mark = new ImageView(this); mark.setImageResource(R.drawable.ic_badge); mark.setScaleType(ImageView.ScaleType.FIT_CENTER);
        form.addView(mark, new LinearLayout.LayoutParams(dp(80), dp(80))); gap(form, 28);
        TextView name = text("WatchCraft", 34, TEXT, true); form.addView(name);
        TextView intro = text("Your world. Your server.", 21, TEXT, false); form.addView(intro); gap(form, 12);
        form.addView(text("Connect to your WatchCraft to watch, read, discover and pick up where you left off.", 16, MUTED, false)); gap(form, 32);
        LinearLayout card = column(); card.setPadding(dp(20), dp(22), dp(20), dp(20)); card.setBackground(round(PANEL, 24));
        card.addView(text("Server address", 15, TEXT, true)); gap(card, 10);
        EditText address = new EditText(this); address.setSingleLine(true); address.setTextSize(17); address.setTextColor(TEXT);
        address.setHintTextColor(MUTED); address.setHint("https://wc.tdgs.org");
        address.setText(server.isEmpty() ? "https://wc.tdgs.org" : server);
        address.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        address.setImeOptions(EditorInfo.IME_ACTION_GO); address.setSelectAllOnFocus(true);
        address.setBackground(outline(BG, 14)); address.setPadding(dp(14), dp(12), dp(14), dp(12));
        card.addView(address, new LinearLayout.LayoutParams(-1, -2)); gap(card, 10);
        CheckBox http = new CheckBox(this); http.setText("Allow an unencrypted HTTP connection"); http.setTextSize(13); http.setTextColor(MUTED);
        http.setButtonTintList(ColorStateList.valueOf(AMBER)); http.setChecked(prefs.getBoolean("http", false));
        card.addView(http); gap(card, 8);
        TextView feedback = text("Use your own server or keep the address above.", 14, MUTED, false);
        feedback.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); card.addView(feedback); gap(card, 18);
        Button connect = action("Connect to WatchCraft", true); card.addView(connect, new LinearLayout.LayoutParams(-1, dp(54)));
        View.OnClickListener start = v -> {
            final String target;
            try { target = ServerAddress.normalize(address.getText().toString(), http.isChecked()); }
            catch (IllegalArgumentException e) { feedback.setText(e.getMessage()); feedback.setTextColor(0xffff8a9f); return; }
            int attempt = ++connectionAttempt;
            connect.setEnabled(false); connect.setText("Connecting…"); address.setEnabled(false); http.setEnabled(false);
            feedback.setText("Checking your WatchCraft server…"); feedback.setTextColor(MUTED);
            io.execute(() -> {
                String issue = checkServer(target);
                runOnUiThread(() -> {
                    if (destroyed || attempt != connectionAttempt) return;
                    connect.setEnabled(true); connect.setText("Connect to WatchCraft"); address.setEnabled(true); http.setEnabled(true);
                    if (issue != null) { feedback.setText(issue); feedback.setTextColor(0xffff8a9f); return; }
                    ((InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(address.getWindowToken(), 0);
                    boolean changed = !target.equals(server);
                    server = target; prefs.edit().putString("server", server).putBoolean("http", http.isChecked()).apply();
                    hasConnected = true; if (changed) currentPath = "/";
                    showService(null);
                });
            });
        };
        connect.setOnClickListener(start);
        address.setOnEditorActionListener((v, action, event) -> { if (action == EditorInfo.IME_ACTION_GO && connect.isEnabled()) { start.onClick(v); return true; } return false; });
        form.addView(card); gap(form, 22);
        form.addView(text("Your content stays on your server. No WatchCraft cloud account needed.", 13, MUTED, false));
        if (hasConnected) { gap(form, 18); Button cancel = action("Back to WatchCraft", false); cancel.setOnClickListener(v -> showService(null)); form.addView(cancel); }
        scroll.addView(outer); root.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
    }

    /** Only known, same-origin JSON qualifies. Never follow a login/redirect to another host. */
    private String checkServer(String target) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection)new URL(target + "/api/health").openConnection();
            connection.setConnectTimeout(12000); connection.setReadTimeout(12000); connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("Accept", "application/json");
            int code = connection.getResponseCode();
            if (code >= 300 && code < 400) return "This address redirects. Enter the final WatchCraft HTTPS address instead.";
            if (code != 200) return "The server returned HTTP " + code + ". Check the address and try again.";
            try (InputStream in = connection.getInputStream()) {
                java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
                byte[] chunk = new byte[1024]; int n;
                while (buffer.size() <= 8192 && (n = in.read(chunk, 0, Math.min(chunk.length, 8193 - buffer.size()))) != -1) buffer.write(chunk, 0, n);
                byte[] data = buffer.toByteArray();
                if (data.length > 8192 || !new JSONObject(new String(data, StandardCharsets.UTF_8)).optBoolean("ok"))
                    return "This address did not return a WatchCraft health response. Check the server address.";
            }
            return null;
        } catch (javax.net.ssl.SSLException e) { return "The server's HTTPS certificate could not be verified. Check its certificate and address."; }
        catch (java.net.UnknownHostException e) { return "Server name not found. Check the address, network or VPN connection."; }
        catch (java.net.SocketTimeoutException e) { return "The server took too long to respond. Check your connection and try again."; }
        catch (Exception e) { return "Could not connect to WatchCraft. Check the address and that your server is running."; }
        finally { if (connection != null) connection.disconnect(); }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void showService(Bundle saved) {
        denyMicrophone();
        if (fileCallback != null) { fileCallback.onReceiveValue(null); fileCallback = null; }
        exportBytes = null; pendingDownload = null; downloadCookie = null;
        root.removeAllViews();
        shell = column(); root.addView(shell, new LinearLayout.LayoutParams(-1, -1));
        LinearLayout header = row(); header.setGravity(Gravity.CENTER_VERTICAL); header.setPadding(dp(14), 0, dp(6), 0);
        ImageView logo = new ImageView(this); logo.setImageResource(R.drawable.ic_badge); logo.setScaleType(ImageView.ScaleType.FIT_CENTER);
        header.addView(logo, new LinearLayout.LayoutParams(dp(30), dp(30)));
        heading = text("WatchCraft", 17, TEXT, true);
        LinearLayout.LayoutParams title = new LinearLayout.LayoutParams(0, -2, 1); title.leftMargin = dp(10);
        header.addView(heading, title);
        header.addView(iconButton("bell", "Notifications", v -> navigate("/alerts")));
        header.addView(iconButton("more", "Page options", v -> pageOptions()));
        shell.addView(header, new LinearLayout.LayoutParams(-1, dp(52)));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setProgressTintList(ColorStateList.valueOf(AMBER)); progress.setProgressBackgroundTintList(ColorStateList.valueOf(PANEL));
        shell.addView(progress, new LinearLayout.LayoutParams(-1, dp(2)));
        content = new FrameLayout(this); shell.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        if (web != null) { web.stopLoading(); web.destroy(); }
        pageReady = false; web = new WebView(this); web.setBackgroundColor(BG);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false); settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true); settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setUserAgentString(settings.getUserAgentString() + " WatchCraftAndroid/" + BuildConfig.VERSION_NAME);
        settings.setBuiltInZoomControls(false); settings.setLoadWithOverviewMode(true); settings.setUseWideViewPort(true);
        CookieManager.getInstance().setAcceptCookie(true); CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        web.setWebViewClient(new ServiceClient()); web.setWebChromeClient(new ServiceChrome());
        web.setDownloadListener(this::download);
        content.addView(web, new FrameLayout.LayoutParams(-1, -1));
        buildBottomBar();
        boolean restored = saved != null && server.equals(saved.getString("webServer")) && web.restoreState(saved) != null;
        if (restored && !ServerAddress.sameOrigin(server, web.getUrl())) restored = false;
        if (!restored) navigate(currentPath); else updateLocation(web.getUrl());
    }

    private void buildBottomBar() {
        if (bottom != null && bottom.getParent() == shell) shell.removeView(bottom);
        bottom = row(); bottom.setBackground(outline(BG, 0)); bottom.setPadding(dp(4), dp(5), dp(4), dp(5));
        String[] shortcuts = shortcutPaths();
        String[][] tabs = new String[5][];
        for (int i = 0; i < 4; i++) {
            Feature f = Navigation.feature(shortcuts[i]);
            tabs[i] = new String[]{f.path.equals("/") ? "TV ▴" : f.title, f.icon, f.path};
        }
        tabs[4] = new String[]{"More", "more", "more"};
        for (String[] tab : tabs) {
            LinearLayout item = column(); item.setGravity(Gravity.CENTER); item.setTag(tab[2]);
            item.setBackground(ripple(round(BG, 18))); item.setContentDescription(tab[2].equals("/") ? "TV menu" : tab[0]);
            item.setFocusable(true); item.addView(icon(tab[1], MUTED, 23), new LinearLayout.LayoutParams(dp(24), dp(24)));
            TextView label = text(tab[0], 11, MUTED, true); label.setGravity(Gravity.CENTER); label.setMaxLines(2); label.setEllipsize(android.text.TextUtils.TruncateAt.END); item.addView(label);
            item.setOnClickListener(v -> { v.performHapticFeedback(android.view.HapticFeedbackConstants.CLOCK_TICK); if (tab[2].equals("more")) showMenu(); else if (tab[2].equals("/")) showTvMenu(v); else navigate(tab[2]); });
            bottom.addView(item, new LinearLayout.LayoutParams(0, dp(56), 1));
        }
        shell.addView(bottom, new LinearLayout.LayoutParams(-1, -2));
    }

    private String[] shortcutPaths() {
        String[] saved = new String[4];
        if (!prefs.getBoolean("navigation_four_slots", false)) {
            // Older releases fixed TV first and stored only the three middle choices.
            saved[0] = "/";
            for (int i = 0; i < 3; i++) saved[i + 1] = prefs.getString("navigation_slot_" + i, null);
        } else for (int i = 0; i < 4; i++) saved[i] = prefs.getString("navigation_slot_" + i, Navigation.DEFAULTS[i]);
        return Navigation.restore(saved);
    }

    private void saveShortcuts(String[] paths) {
        SharedPreferences.Editor editor = prefs.edit();
        editor.putBoolean("navigation_four_slots", true);
        for (int i = 0; i < 4; i++) editor.putString("navigation_slot_" + i, paths[i]);
        editor.apply(); buildBottomBar(); updateLocation(server + currentPath);
    }

    private void showTvMenu(View anchor) {
        PopupMenu popup = new PopupMenu(this, anchor);
        String[] titles = {"TV home", "Playlists", "Feeds", "Music videos"};
        String[] paths = {"/", "/settings?tab=categories", "/shorts", "/music"};
        for (int i = 0; i < titles.length; i++) popup.getMenu().add(0, i, i, titles[i]);
        popup.setOnMenuItemClickListener(item -> { navigate(paths[item.getItemId()]); return true; });
        popup.show();
    }

    private void navigationSettings() {
        String[] paths = shortcutPaths();
        String[] titles = new String[4];
        for (int i = 0; i < 4; i++) titles[i] = "Button " + (i + 1) + ": " + Navigation.feature(paths[i]).title;
        new AlertDialog.Builder(this).setTitle("Bottom bar shortcuts")
                .setItems(titles, (dialog, slot) -> chooseShortcut(slot))
                .setNeutralButton("Reset defaults", (dialog, which) -> { saveShortcuts(Navigation.DEFAULTS.clone()); navigationSettings(); })
                .setPositiveButton("Done", null).show();
    }

    private void chooseShortcut(int slot) {
        String[] paths = shortcutPaths();
        Feature[] choices = Navigation.choices(); String[] names = new String[choices.length]; int selected = -1;
        for (int i = 0; i < choices.length; i++) { names[i] = choices[i].title; if (choices[i].path.equals(paths[slot])) selected = i; }
        new AlertDialog.Builder(this).setTitle("Choose button " + (slot + 1))
                .setSingleChoiceItems(names, selected, (dialog, which) -> {
                    Navigation.replace(paths, slot, choices[which].path); saveShortcuts(paths); dialog.dismiss(); navigationSettings();
                }).setNegativeButton("Cancel", (dialog, which) -> navigationSettings()).show();
    }

    private void navigate(String path) {
        if (web == null) return;
        denyMicrophone(); clearError();
        if (pageReady && !loadingFailed && ServerAddress.sameOrigin(server, web.getUrl())) {
            // Let SvelteKit keep the current session and page shell during native-tab navigation.
            web.evaluateJavascript("(()=>{const a=document.createElement('a');a.href=" + JSONObject.quote(server + path)
                    + ";a.hidden=true;document.body.append(a);a.click();a.remove();})()", null);
        } else web.loadUrl(server + path);
    }

    private void updateLocation(String url) {
        if (!ServerAddress.sameOrigin(server, url)) return;
        String path = Uri.parse(url).getPath(); if (path == null) path = "/";
        currentPath = path + (Uri.parse(url).getEncodedQuery() == null ? "" : "?" + Uri.parse(url).getEncodedQuery());
        heading.setText(Feature.titleFor(path));
        boolean playlists = path.equals("/settings") && "categories".equals(Uri.parse(url).getQueryParameter("tab"));
        int selected = 4;
        for (int i = 0; i < 4; i++) {
            String destination = (String) bottom.getChildAt(i).getTag();
            boolean tvPage = playlists || path.equals("/") || path.equals("/tv") || path.startsWith("/watch/") || path.startsWith("/category/") || path.startsWith("/channel/") || path.equals("/shorts") || path.startsWith("/shorts/") || path.equals("/music") || path.startsWith("/music/");
            if ((destination.equals("/") && tvPage && selected == 4) || (!destination.equals("/") && !playlists && Navigation.matches(destination, path))
                    || (destination.equals("/news") && path.equals("/today"))) selected = i;
        }
        for (int i = 0; i < bottom.getChildCount(); i++) {
            LinearLayout tab = (LinearLayout)bottom.getChildAt(i); boolean on = i == selected;
            tab.setSelected(on); tab.setBackground(ripple(round(on ? PANEL : BG, 18)));
            ((ImageView)tab.getChildAt(0)).setColorFilter(on ? AMBER : MUTED);
            ((TextView)tab.getChildAt(1)).setTextColor(on ? AMBER : MUTED);
        }
    }

    private final class ServiceClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            String url = request.getUrl().toString();
            if (!request.isForMainFrame()) return false;
            if (ServerAddress.sameOrigin(server, url)) return false;
            denyMicrophone(); openExternal(url); return true;
        }
        @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
            denyMicrophone();
            if (!ServerAddress.sameOrigin(server, url)) { view.stopLoading(); showError("This page is outside your WatchCraft server."); return; }
            pageReady = false; loadingFailed = false; clearError(); progress.setVisibility(View.VISIBLE); updateLocation(url);
        }
        @Override public void onPageFinished(WebView view, String url) {
            if (!ServerAddress.sameOrigin(server, url)) return;
            pageReady = !loadingFailed; progress.setVisibility(View.INVISIBLE); CookieManager.getInstance().flush();
            // Older servers work too: remove only the duplicate web navigation, leaving profile/AI controls intact.
            view.evaluateJavascript("(()=>{let s=document.getElementById('wc-android-shell');if(!s){s=document.createElement('style');s.id='wc-android-shell';document.head.append(s)}s.textContent=" + JSONObject.quote(SHELL_CSS) + ";})()", null);
            if (!loadingFailed) clearError(); updateLocation(url);
        }
        @Override public void doUpdateVisitedHistory(WebView view, String url, boolean reload) { denyMicrophone(); updateLocation(url); }
        @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) { loadingFailed = true; showError("Can't reach your WatchCraft server. Check your network or VPN, then try again."); }
        }
        @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
            if (request.isForMainFrame()) { loadingFailed = true; showError("Your server returned HTTP " + response.getStatusCode() + ". Try again or check the server address."); }
        }
        @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            handler.cancel(); loadingFailed = true; showError("The HTTPS certificate could not be verified. Check your server's certificate before reconnecting.");
        }
        @Override public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
            if (view != web) return true;
            content.removeView(web); web.destroy(); web = null;
            showError("The page was closed by Android. Reconnect to continue."); return true;
        }
    }

    private final class ServiceChrome extends WebChromeClient {
        @Override public void onProgressChanged(WebView view, int value) {
            progress.setProgress(value); progress.setVisibility(value == 100 ? View.INVISIBLE : View.VISIBLE);
        }
        @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            if (!ServerAddress.sameOrigin(server, view.getUrl())) { callback.onReceiveValue(null); return true; }
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = callback;
            Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT); pick.addCategory(Intent.CATEGORY_OPENABLE); pick.setType("*/*");
            String[] types = AcceptTypes.mimeTypes(params.getAcceptTypes(), MimeTypeMap.getSingleton()::getMimeTypeFromExtension);
            if (types.length > 0) pick.putExtra(Intent.EXTRA_MIME_TYPES, types);
            pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
            try { startActivityForResult(pick, PICK_FILE); }
            catch (ActivityNotFoundException e) { fileCallback.onReceiveValue(null); fileCallback = null; toast("No file picker is available."); }
            return true;
        }
        @Override public void onShowCustomView(View view, CustomViewCallback callback) {
            if (customView != null) { callback.onCustomViewHidden(); return; }
            customView = view; customCallback = callback;
            root.addView(view, new LinearLayout.LayoutParams(-1, -1)); shell.setVisibility(View.GONE);
            root.setPadding(0, 0, 0, 0);
            getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            if (Build.VERSION.SDK_INT >= 30) {
                WindowInsetsController control = getWindow().getInsetsController();
                if (control != null) { control.hide(WindowInsets.Type.systemBars()); control.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE); }
            } else getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
        }
        @Override public void onHideCustomView() { exitFullscreen(); }
        @Override public void onPermissionRequest(PermissionRequest request) {
            if (!ServerAddress.sameOrigin(server, request.getOrigin().toString()) || !ServerAddress.sameOrigin(server, web.getUrl())) { request.deny(); return; }
            boolean audio = false;
            for (String resource : request.getResources()) if (resource.equals(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) audio = true;
            if (!audio || !server.startsWith("https://")) { request.deny(); return; }
            denyMicrophone(); microphoneRequest = request;
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) grantMicrophone();
            else { requestingMicrophone = true; requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, RECORD_AUDIO); }
        }
        @Override public void onPermissionRequestCanceled(PermissionRequest request) { if (microphoneRequest == request) microphoneRequest = null; }
        @Override public boolean onCreateWindow(WebView view, boolean dialog, boolean userGesture, Message message) {
            if (!userGesture) return false;
            WebView popup = new WebView(MainActivity.this);
            popup.setWebViewClient(new WebViewClient() {
                private boolean handled;
                @Override public void onPageStarted(WebView child, String url, android.graphics.Bitmap favicon) {
                    if (handled || url.equals("about:blank")) return;
                    handled = true; child.stopLoading();
                    if (ServerAddress.sameOrigin(server, url)) web.loadUrl(url); else openExternal(url);
                    child.destroy();
                }
            });
            ((WebView.WebViewTransport)message.obj).setWebView(popup); message.sendToTarget(); return true;
        }
    }

    private void grantMicrophone() {
        PermissionRequest request = microphoneRequest; microphoneRequest = null;
        if (request == null) return;
        if (web != null && ServerAddress.sameOrigin(server, request.getOrigin().toString()) && ServerAddress.sameOrigin(server, web.getUrl()))
            request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
        else request.deny();
    }
    private void denyMicrophone() { if (microphoneRequest != null) { microphoneRequest.deny(); microphoneRequest = null; } }
    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == RECORD_AUDIO) {
            requestingMicrophone = false;
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) grantMicrophone();
            else { denyMicrophone(); toast("Microphone access was denied. You can still type your search."); }
        }
    }

    private void showError(String message) {
        clearError(); progress.setVisibility(View.INVISIBLE);
        LinearLayout box = column(); box.setGravity(Gravity.CENTER); box.setPadding(dp(28), dp(28), dp(28), dp(28)); box.setBackgroundColor(BG);
        ImageView symbol = icon("server", AMBER, 42); box.addView(symbol, new LinearLayout.LayoutParams(dp(48), dp(48))); gap(box, 24);
        TextView title = text("Connection interrupted", 24, TEXT, true); title.setGravity(Gravity.CENTER); box.addView(title); gap(box, 12);
        TextView detail = text(message, 16, MUTED, false); detail.setGravity(Gravity.CENTER); detail.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_ASSERTIVE); box.addView(detail); gap(box, 28);
        Button retry = action("Try again", true); retry.setOnClickListener(v -> { if (web == null) showService(null); else { clearError(); web.reload(); } }); box.addView(retry);
        Button settings = action("Change server", false); settings.setOnClickListener(v -> showSetup()); box.addView(settings);
        errorPanel = box; content.addView(box, new FrameLayout.LayoutParams(-1, -1));
    }
    private void clearError() { if (errorPanel != null && content != null) content.removeView(errorPanel); errorPanel = null; }

    private void showMenu() {
        if (menu != null) menu.dismiss();
        menu = new Dialog(this); ScrollView scroll = new ScrollView(this);
        LinearLayout sheet = column(); sheet.setBackground(round(PANEL, 28)); sheet.setPadding(dp(20), dp(24), dp(20), dp(24));
        LinearLayout top = row(); top.setGravity(Gravity.CENTER_VERTICAL); top.addView(text("Your WatchCraft", 24, TEXT, true), new LinearLayout.LayoutParams(0, -2, 1));
        Button close = action("Done", false); close.setOnClickListener(v -> menu.dismiss()); top.addView(close); sheet.addView(top);
        String group = "";
        for (Feature f : Feature.ALL) {
            if (!group.equals(f.group)) { gap(sheet, 20); sheet.addView(text(f.group, 14, AMBER, true)); gap(sheet, 8); group = f.group; }
            LinearLayout item = row(); item.setGravity(Gravity.CENTER_VERTICAL); item.setPadding(dp(12), dp(12), dp(12), dp(12)); item.setBackground(ripple(round(PANEL, 12)));
            item.addView(icon(f.icon, MUTED, 23), new LinearLayout.LayoutParams(dp(24), dp(24)));
            TextView title = text(f.title, 16, TEXT, false); title.setPadding(dp(16), 0, 0, 0); item.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
            item.addView(icon("arrow", MUTED, 20), new LinearLayout.LayoutParams(dp(20), dp(20)));
            item.setMinimumHeight(dp(52)); item.setContentDescription(f.title); item.setFocusable(true);
            item.setOnClickListener(v -> { menu.dismiss(); navigate(f.path); }); sheet.addView(item);
        }
        gap(sheet, 24); Button navigation = action("Customize bottom bar", false); navigation.setOnClickListener(v -> { menu.dismiss(); navigationSettings(); }); sheet.addView(navigation);
        gap(sheet, 12); Button connection = action("Server & app", false); connection.setOnClickListener(v -> { menu.dismiss(); connectionOptions(); }); sheet.addView(connection);
        scroll.addView(sheet); menu.setContentView(scroll);
        Window w = menu.getWindow(); if (w != null) { w.setBackgroundDrawableResource(android.R.color.transparent); w.setGravity(Gravity.BOTTOM); w.setDimAmount(.55f); }
        menu.show(); if (w != null) w.setLayout(Math.min(getResources().getDisplayMetrics().widthPixels, dp(560)), (int)(getResources().getDisplayMetrics().heightPixels * .86));
    }

    private void pageOptions() {
        String[] options = {"Search", "Share this page", "Refresh", "Open in browser", "Server & app"};
        new AlertDialog.Builder(this).setTitle(Feature.titleFor(Uri.parse(server + currentPath).getPath())).setItems(options, (dialog, which) -> {
            if (which == 0) search();
            else if (which == 1) {
                Intent share = new Intent(Intent.ACTION_SEND); share.setType("text/plain"); share.putExtra(Intent.EXTRA_TEXT, pageUrl()); startActivity(Intent.createChooser(share, "Share WatchCraft page"));
            } else if (which == 2) { if (web == null) showService(null); else web.reload(); }
            else if (which == 3) openExternal(pageUrl()); else connectionOptions();
        }).show();
    }

    private String pageUrl() { String url = web == null ? null : web.getUrl(); return ServerAddress.sameOrigin(server, url) ? url : server + "/"; }
    private void connectionOptions() {
        new AlertDialog.Builder(this).setTitle("Server & app")
                .setMessage("Connected to " + server + "\n\nWatchCraft " + BuildConfig.VERSION_NAME + "\nYour profile and settings belong to this server.")
                .setPositiveButton("Change server", (d, w) -> showSetup())
                .setNeutralButton("Updates", (d, w) -> openExternal("https://github.com/icewall905/watchcraft/releases"))
                .setNegativeButton("Close", null).show();
    }

    private void search() {
        EditText query = new EditText(this); query.setSingleLine(true); query.setHint("Search your WatchCraft"); query.setTextColor(TEXT); query.setTextSize(18);
        query.setPadding(dp(24), dp(16), dp(24), dp(16)); query.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Find something good")
                .setView(query).setPositiveButton("Search", (d, w) -> navigate("/find?q=" + Uri.encode(query.getText().toString().trim())))
                .setNegativeButton("Cancel", null).create();
        query.setOnEditorActionListener((v, action, event) -> { if (action == EditorInfo.IME_ACTION_SEARCH) { dialog.dismiss(); navigate("/find?q=" + Uri.encode(query.getText().toString().trim())); return true; } return false; });
        dialog.setOnShowListener(d -> { query.requestFocus(); dialog.getWindow().setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE); }); dialog.show();
    }

    private void openExternal(String url) {
        Uri uri = Uri.parse(url); String scheme = uri.getScheme();
        if (!(ServerAddress.isWebLink(url) || "mailto".equals(scheme) || "tel".equals(scheme))) { toast("This link type is not supported."); return; }
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE)); }
        catch (ActivityNotFoundException e) { toast("No app is available to open this link."); }
    }

    private void download(String url, String userAgent, String disposition, String mime, long size) {
        if (url.startsWith("blob:")) { exportBlob(url, mime); return; }
        if (!ServerAddress.sameOrigin(server, url)) { openExternal(url); return; }
        String name = URLUtil.guessFileName(url, disposition, mime).replaceAll("[^a-zA-Z0-9._ -]", "_");
        pendingDownload = url; downloadOrigin = server;
        downloadCookie = CookieManager.getInstance().getCookie(url); downloadAgent = userAgent;
        Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT); save.addCategory(Intent.CATEGORY_OPENABLE);
        save.setType(mime == null || mime.isEmpty() ? "application/octet-stream" : mime);
        save.putExtra(Intent.EXTRA_TITLE, name.trim().isEmpty() ? "watchcraft-download" : name);
        try { startActivityForResult(save, DOWNLOAD_FILE); }
        catch (ActivityNotFoundException e) { pendingDownload = null; toast("No file picker is available. Try opening this page in your browser."); }
    }

    /** Redirects stay within the server origin so cookies never reach third-party download hosts. */
    private void saveDownload(String url, String origin, String cookie, String agent, Uri destination) {
        HttpURLConnection connection = null;
        try {
            String next = url;
            for (int redirects = 0; redirects <= 5; redirects++) {
                if (!ServerAddress.sameOrigin(origin, next)) throw new java.io.IOException("External download redirect blocked");
                connection = (HttpURLConnection)new URL(next).openConnection();
                connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(15000); connection.setReadTimeout(30000);
                if (cookie != null) connection.setRequestProperty("Cookie", cookie);
                if (agent != null) connection.setRequestProperty("User-Agent", agent);
                int code = connection.getResponseCode();
                if (code >= 300 && code < 400) {
                    String location = connection.getHeaderField("Location");
                    if (location == null) throw new java.io.IOException("Missing redirect address");
                    next = new URL(new URL(next), location).toString(); connection.disconnect(); connection = null; continue;
                }
                if (code != 200) throw new java.io.IOException("HTTP " + code);
                try (InputStream in = connection.getInputStream(); java.io.OutputStream out = getContentResolver().openOutputStream(destination, "wt")) {
                    if (out == null) throw new java.io.IOException("No destination");
                    byte[] block = new byte[65536]; int n;
                    while (!Thread.currentThread().isInterrupted() && (n = in.read(block)) != -1) out.write(block, 0, n);
                    if (Thread.currentThread().isInterrupted()) throw new java.io.IOException("Download interrupted");
                }
                runOnUiThread(() -> toast("Download saved.")); return;
            }
            throw new java.io.IOException("Too many redirects");
        } catch (Exception e) {
            try { android.provider.DocumentsContract.deleteDocument(getContentResolver(), destination); } catch (Exception ignored) { /* Some providers do not allow deleting the partial document. */ }
            runOnUiThread(() -> toast("Download failed. Check your connection or open this page in your browser."));
        } finally { if (connection != null) connection.disconnect(); }
    }

    private void exportBlob(String url, String mime) {
        if (web == null || !ServerAddress.sameOrigin(server, web.getUrl()) || !url.startsWith("blob:" + server + "/")) { toast("This download is unavailable."); return; }
        String origin = server;
        String script = "(async()=>{try{const b=await(await fetch(" + JSONObject.quote(url) + ")).blob();if(b.size>" + MAX_EXPORT_BYTES
                + ")return '';return await new Promise(r=>{let f=new FileReader();f.onload=()=>r(f.result);f.onerror=()=>r('');f.readAsDataURL(b)})}catch(e){return ''}})()";
        // evaluateJavascript does not await promises; resolve via a one-off DOM value and poll, without a JS bridge.
        String token = "__wcExport" + System.nanoTime();
        web.evaluateJavascript("window[" + JSONObject.quote(token) + "]=null;" + script + ".then(v=>window[" + JSONObject.quote(token) + "]=v)", null);
        pollExport(token, origin, mime, 0);
    }
    private void pollExport(String token, String origin, String mime, int count) {
        if (destroyed || web == null || !origin.equals(server) || !ServerAddress.sameOrigin(origin, web.getUrl())) return;
        web.postDelayed(() -> {
            if (destroyed || web == null || !origin.equals(server) || !ServerAddress.sameOrigin(origin, web.getUrl())) return;
            web.evaluateJavascript("window[" + JSONObject.quote(token) + "]", result -> {
                if (destroyed || web == null || !origin.equals(server) || !ServerAddress.sameOrigin(origin, web.getUrl())) return;
                if (result == null || result.equals("null")) { if (count < 50) pollExport(token, origin, mime, count + 1); else toast("Export timed out. Try your browser."); return; }
                web.evaluateJavascript("delete window[" + JSONObject.quote(token) + "]", null);
                try {
                    Object value = new JSONTokener(result).nextValue(); if (!(value instanceof String)) throw new IllegalArgumentException();
                    String data = (String)value; int comma = data.indexOf(','); if (comma < 0 || data.length() > MAX_EXPORT_BYTES * 1.4) throw new IllegalArgumentException();
                    exportBytes = android.util.Base64.decode(data.substring(comma + 1), android.util.Base64.DEFAULT); exportOrigin = origin;
                    Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT); save.addCategory(Intent.CATEGORY_OPENABLE); save.setType(mime == null || mime.isEmpty() ? "application/octet-stream" : mime);
                    save.putExtra(Intent.EXTRA_TITLE, mime != null && mime.contains("json") ? "watchcraft-export.json" : "watchcraft-export"); startActivityForResult(save, EXPORT_FILE);
                } catch (Exception e) { exportBytes = null; toast("This export could not be saved. Exports up to 16 MB are supported; try your browser for larger files."); }
            });
        }, 200);
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == PICK_FILE && fileCallback != null) {
            Uri[] uris = null;
            if (result == RESULT_OK && data != null && web != null && ServerAddress.sameOrigin(server, web.getUrl())) {
                ClipData clip = data.getClipData();
                if (clip != null) { uris = new Uri[clip.getItemCount()]; for (int i = 0; i < uris.length; i++) uris[i] = clip.getItemAt(i).getUri(); }
                else if (data.getData() != null) uris = new Uri[]{data.getData()};
            }
            fileCallback.onReceiveValue(uris); fileCallback = null;
        } else if (request == DOWNLOAD_FILE) {
            String url = pendingDownload, origin = downloadOrigin, cookie = downloadCookie, agent = downloadAgent;
            pendingDownload = null; downloadCookie = null;
            if (result == RESULT_OK && data != null && data.getData() != null && url != null && server.equals(origin)) {
                Uri destination = data.getData(); toast("Downloading… Keep WatchCraft open until the file is saved.");
                io.execute(() -> saveDownload(url, origin, cookie, agent, destination));
            }
        } else if (request == EXPORT_FILE) {
            byte[] bytes = exportBytes; exportBytes = null;
            if (result == RESULT_OK && data != null && data.getData() != null && bytes != null && server.equals(exportOrigin)) {
                Uri dest = data.getData(); io.execute(() -> {
                    try (java.io.OutputStream out = getContentResolver().openOutputStream(dest)) { if (out == null) throw new java.io.IOException(); out.write(bytes); runOnUiThread(() -> toast("Export saved.")); }
                    catch (Exception e) { runOnUiThread(() -> toast("Could not save this export. Check available storage and try again.")); }
                });
            }
        }
    }

    private void exitFullscreen() {
        if (customView == null) return;
        root.removeView(customView); customView = null; shell.setVisibility(View.VISIBLE);
        if (customCallback != null) { customCallback.onCustomViewHidden(); customCallback = null; }
        getWindow().clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= 30) { WindowInsetsController c = getWindow().getInsetsController(); if (c != null) c.show(WindowInsets.Type.systemBars()); }
        else getWindow().getDecorView().setSystemUiVisibility(0);
        root.requestApplyInsets();
    }
    private void goBack() {
        if (customView != null) { exitFullscreen(); return; }
        if (menu != null && menu.isShowing()) { menu.dismiss(); return; }
        if (shell == null || shell.getParent() == null) { if (hasConnected) showService(null); else finish(); return; }
        if (web != null && web.canGoBack()) { clearError(); web.goBack(); }
        else if (!currentPath.equals("/")) navigate("/"); else finish();
    }
    @Override public void onConfigurationChanged(Configuration c) { super.onConfigurationChanged(c); root.requestApplyInsets(); }
    @Override protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        if (web != null && ServerAddress.sameOrigin(server, web.getUrl())) { web.saveState(state); state.putString("webServer", server); }
    }
    @Override protected void onPause() { if (web != null) { web.onPause(); CookieManager.getInstance().flush(); } if (!requestingMicrophone) denyMicrophone(); super.onPause(); }
    @Override protected void onResume() { super.onResume(); if (web != null) web.onResume(); }
    @Override protected void onDestroy() {
        destroyed = true; connectionAttempt++; denyMicrophone();
        if (fileCallback != null) { fileCallback.onReceiveValue(null); fileCallback = null; }
        if (menu != null) menu.dismiss();
        if (web != null) { web.stopLoading(); web.destroy(); web = null; }
        io.shutdownNow(); super.onDestroy();
    }

    private int dp(float value) { return (int)(value * getResources().getDisplayMetrics().density + .5f); }
    private LinearLayout column() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL); return l; }
    private LinearLayout row() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.HORIZONTAL); return l; }
    private TextView text(String value, int size, int color, boolean bold) {
        TextView t = new TextView(this); t.setText(value); t.setTextSize(size); t.setTextColor(color);
        t.setTypeface(Typeface.create("sans-serif", bold ? Typeface.BOLD : Typeface.NORMAL)); t.setLineSpacing(dp(3), 1); return t;
    }
    private ImageView icon(String name, int color, int size) { ImageView v = new ImageView(this); v.setImageDrawable(new LineIcon(name, color)); v.setMinimumWidth(dp(size)); v.setMinimumHeight(dp(size)); return v; }
    private View iconButton(String icon, String description, View.OnClickListener listener) {
        ImageView v = icon(icon, TEXT, 24); v.setPadding(dp(13), dp(13), dp(13), dp(13)); v.setContentDescription(description);
        v.setBackground(ripple(round(BG, 24))); v.setOnClickListener(listener); v.setFocusable(true); v.setLayoutParams(new LinearLayout.LayoutParams(dp(48), dp(48))); return v;
    }
    private Button action(String title, boolean primary) {
        Button b = new Button(this); b.setText(title); b.setAllCaps(false); b.setTextSize(16); b.setTextColor(primary ? INK : AMBER);
        b.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL)); b.setBackground(ripple(round(primary ? AMBER : Color.TRANSPARENT, 18)));
        b.setPadding(dp(18), dp(10), dp(18), dp(10)); b.setMinHeight(dp(48)); b.setMinimumHeight(dp(48)); return b;
    }
    private GradientDrawable round(int fill, int radius) { GradientDrawable d = new GradientDrawable(); d.setColor(fill); d.setCornerRadius(dp(radius)); return d; }
    private GradientDrawable outline(int fill, int radius) { GradientDrawable d = round(fill, radius); d.setStroke(dp(1), LINE); return d; }
    private RippleDrawable ripple(android.graphics.drawable.Drawable background) { return new RippleDrawable(ColorStateList.valueOf(0x339d86ff), background, round(Color.WHITE, 18)); }
    private void gap(LinearLayout parent, int height) { parent.addView(new View(this), new LinearLayout.LayoutParams(1, dp(height))); }
    private void toast(String message) { if (!destroyed) Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }
}
