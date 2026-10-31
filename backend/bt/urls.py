"""URL rules shared by the hardened fetcher and the watcher child process (no heavy imports)."""
from urllib.parse import urlsplit, urlunsplit


def canonical(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Use a public HTTPS URL on port 443.')
    return urlunsplit(('https', p.netloc.lower(), p.path or '/', p.query, ''))
