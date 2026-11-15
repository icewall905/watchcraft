/** Kid-safe lock, mirrored from the server (hub/providers.py KID_PROVIDERS): the only
 * sources a child profile's curators may use. The server enforces this; the UI only hides the rest. */
export const KID_PROVIDERS = ['web_search', 'bing_news', 'wikipedia'];
export const isKidProvider = (key: string) => KID_PROVIDERS.includes(key);
