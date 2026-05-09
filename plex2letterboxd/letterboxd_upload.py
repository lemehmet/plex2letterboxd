"""Upload watched-history entries to Letterboxd by impersonating the import wizard.

Letterboxd has no public API endpoint for individual developers, so this module
talks to the same web flow the /import/ wizard uses. Three steps:

  1. POST /import/csv/                            -- multipart upload of the CSV.
                                                     Returns the wizard HTML; per
                                                     entry it contains an
                                                     `<li class="import-film"
                                                     data-json="...">` block.

  2. POST /import/watchlist/match-import-film/    -- JSON XHR with the parsed
                                                     films; returns matched-item
                                                     HTML containing the film
                                                     IDs Letterboxd assigned.

  3. POST /s/save-users-imported-imdb-history     -- form POST that commits the
                                                     import using the matched
                                                     IDs and per-entry metadata.

All three require a session cookie (`letterboxd.signed.in.as=...`,
`com.xk72.webparts.csrf=...`, plus a `cf_clearance=...` from a recent browser
session) AND TLS impersonation, since letterboxd.com sits behind Cloudflare's
bot challenge. We use `curl_cffi` with a recent Chrome profile; older profiles
are fingerprinted and rejected with a `Just a moment...` interstitial.

This is reverse-engineered from the public web UI; expect breakage if Letterboxd
changes the import flow.
"""
import json
import os
import re
import tempfile
from html import unescape
from http.cookies import SimpleCookie

try:
    from curl_cffi import requests as _http
    from curl_cffi import CurlMime
    _HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    import requests as _http
    CurlMime = None
    _HAS_CURL_CFFI = False

BASE = 'https://letterboxd.com'
IMPORT_PAGE = f'{BASE}/import/'
UPLOAD_URL = f'{BASE}/import/csv/'
MATCH_URL = f'{BASE}/import/watchlist/match-import-film/'
SAVE_URL = f'{BASE}/s/save-users-imported-imdb-history'

CSRF_COOKIE = 'com.xk72.webparts.csrf'
SIGNED_IN_COOKIE = 'letterboxd.signed.in.as'
# Letterboxd's actual session/auth token. Names vary by client (`...CURRENT`,
# `...USER_TOKEN`, etc.) so we accept any cookie under the `letterboxd.user.`
# namespace. Without it, requests are unauthenticated even if the username
# marker is present.
SESSION_COOKIE_PREFIX = 'letterboxd.user.'

# Chrome 136 was the oldest profile that consistently passed Cloudflare on
# POST requests during testing in May 2026; older profiles (chrome<=131,
# chrome142) get a 403 + "Just a moment" interstitial. If this stops working,
# bump to a newer profile (`chrome146`, `safari260`, etc).
IMPERSONATE = 'chrome136'

USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/136.0.0.0 Safari/537.36'
)

class UploadError(Exception):
    """Raised when the upload flow cannot complete."""


def parse_cookie_string(cookie_string):
    """Parse a cookie header string ('a=1; b=2; ...') into a dict."""
    jar = SimpleCookie()
    jar.load(cookie_string)
    return {k: morsel.value for k, morsel in jar.items()}


def _make_session(cookie_string):
    cookies = parse_cookie_string(cookie_string)
    if CSRF_COOKIE not in cookies:
        raise UploadError(
            f'cookie string is missing {CSRF_COOKIE!r} -- copy the full '
            'Cookie header from a browser session signed in to letterboxd.com'
        )
    if SIGNED_IN_COOKIE not in cookies:
        raise UploadError(
            f'cookie string is missing {SIGNED_IN_COOKIE!r} -- you may not '
            'be signed in, or you copied only a partial cookie header'
        )
    if not any(k.startswith(SESSION_COOKIE_PREFIX) for k in cookies):
        raise UploadError(
            f'cookie string is missing the session token (a cookie named '
            f'{SESSION_COOKIE_PREFIX}* such as {SESSION_COOKIE_PREFIX}CURRENT). '
            'Without it the upload POST is treated as anonymous and redirected '
            'to the sign-in page. Re-copy the full Cookie header from devtools.'
        )
    if not _HAS_CURL_CFFI:
        raise UploadError(
            "curl_cffi is required to bypass Cloudflare's bot challenge. "
            "Install with `pip install 'plex2letterboxd[upload]'` "
            "(or `pipx inject plex2letterboxd curl_cffi`)."
        )
    session = _http.Session(impersonate=IMPERSONATE)
    for name, value in cookies.items():
        session.cookies.set(name, value, domain='.letterboxd.com')
    session.headers.update({
        'User-Agent': USER_AGENT,
    })
    return session


def _csrf(session):
    token = session.cookies.get(CSRF_COOKIE, domain='.letterboxd.com')
    if not token:
        token = session.cookies.get(CSRF_COOKIE)
    if not token:
        raise UploadError('CSRF cookie was cleared by the server (session expired?)')
    return token


def _check_cloudflare(resp, step):
    body = resp.text or ''
    if 'Just a moment' in body or 'cf-challenge' in body or 'cf_chl' in body:
        raise UploadError(
            f'{step} hit Cloudflare challenge. Refresh `cf_clearance` '
            'from a recent browser session, or bump the curl_cffi '
            f'impersonation profile (currently {IMPERSONATE!r}).'
        )


# === step 1: upload CSV ===================================================

# Match each import-film <li> regardless of attribute order. We capture the
# whole tag first, then pull `data-json` and the inner body separately so the
# regex doesn't depend on attribute ordering.
_LI_TAG_RE = re.compile(r'<li\b[^>]*\bclass="[^"]*\bimport-film\b[^"]*"[^>]*>',
                        re.I)
_LI_BODY_RE = re.compile(
    r'(<li\b[^>]*\bclass="[^"]*\bimport-film\b[^"]*"[^>]*>)(.*?)</li>',
    re.S | re.I,
)
_DATA_JSON_RE = re.compile(r'\bdata-json="([^"]*)"')


def _dump_response(resp, prefix):
    """Save a response body for debugging; return the path."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix='.html')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(resp.text)
    return path


def _scan_error_markers(html):
    """Best-effort: surface any obvious 'something went wrong' text from a
    Letterboxd response so the user gets more than 'no entries parsed'."""
    snippets = []
    for pat in (
        r'<h1[^>]*>([^<]+(?:error|fail|invalid|sorry|denied)[^<]*)</h1>',
        r'class="error[^"]*"[^>]*>([^<]+)<',
        r'<p[^>]*class="message[^"]*"[^>]*>([^<]+)<',
        r'<title>([^<]+)</title>',
    ):
        for m in re.finditer(pat, html, re.I):
            text = m.group(1).strip()
            if text and text not in snippets:
                snippets.append(text)
    return snippets[:3]


def _upload_csv(session, csv_bytes):
    """Submit the CSV; return a list of (film_data, per_li_inputs) tuples
    captured from the wizard response."""
    mime = CurlMime()
    mime.addpart(name='__csrf', data=_csrf(session).encode())
    mime.addpart(name='file', filename='letterboxd.csv',
                 content_type='text/csv', data=csv_bytes)
    resp = session.post(UPLOAD_URL, multipart=mime,
                        headers={'Referer': IMPORT_PAGE})
    _check_cloudflare(resp, 'CSV upload')
    if resp.status_code != 200:
        raise UploadError(
            f'CSV upload failed: HTTP {resp.status_code} '
            f'(body starts: {resp.text[:200]!r})'
        )

    items = []
    for match in _LI_BODY_RE.finditer(resp.text):
        opening_tag, inner = match.group(1), match.group(2)
        data_json_match = _DATA_JSON_RE.search(opening_tag)
        if not data_json_match:
            continue
        try:
            data_json = json.loads(unescape(data_json_match.group(1)))
        except json.JSONDecodeError:
            continue
        per_li_fields = {}
        for fname, fval in re.findall(
                r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', inner):
            per_li_fields.setdefault(fname, fval)
        items.append((data_json, per_li_fields))

    if not items:
        debug_path = _dump_response(resp, 'plex2letterboxd-upload-')
        markers = _scan_error_markers(resp.text)
        marker_hint = (' Letterboxd response markers: '
                       + '; '.join(repr(m) for m in markers)) if markers else ''
        raise UploadError(
            f'CSV upload returned a {len(resp.text)}-byte page but no '
            'import-film entries were parsed. The import may have been '
            'rejected, or Letterboxd changed the wizard markup. Full '
            f'response saved to {debug_path} for inspection.{marker_hint}'
        )
    return items


# === step 2: match films to Letterboxd IDs ================================

_FILM_ID_RE = re.compile(r'name="importFilmId"\s*value="(\d+)"')
_MATCHED_ITEM_RE = re.compile(
    r'<div class="matched-item[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</div>',
    re.S,
)


def _match_films(session, import_films):
    """POST the parsed films and return per-entry match data."""
    payload = {'importType': 'diary', 'importFilms': import_films}
    resp = session.post(
        MATCH_URL,
        data={'__csrf': _csrf(session), 'json': json.dumps(payload)},
        headers={
            'Accept': 'text/html, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': BASE,
            'Referer': IMPORT_PAGE,
        },
    )
    _check_cloudflare(resp, 'match step')
    if resp.status_code != 200:
        raise UploadError(
            f'match step failed: HTTP {resp.status_code} '
            f'(body starts: {resp.text[:200]!r})'
        )

    film_ids = _FILM_ID_RE.findall(resp.text)
    viewing_ids = re.findall(r'name="importViewingId"\s*value="([^"]*)"', resp.text)
    # Pad viewing_ids if it's shorter than film_ids (defensive).
    while len(viewing_ids) < len(film_ids):
        viewing_ids.append('')

    if not film_ids:
        raise UploadError(
            'no films matched -- cookie may be expired, or Letterboxd '
            'changed the match response format'
        )
    return list(zip(film_ids, viewing_ids))


# === step 3: commit the import ============================================

def _save(session, items, matches):
    """Submit the final form. `items` is the per-li data from upload step;
    `matches` is the (film_id, viewing_id) list from the match step."""
    fields = [
        ('__csrf', _csrf(session)),
        ('filmListId', ''),
        ('name', ''),
        ('publicList', ''),
        ('numberedList', ''),
        ('notes', ''),
        ('tags', ''),
    ]
    # Form expects per-film tuples in document order: importRating, importReview,
    # importTags, importWatchedDate, importRewatch, importFilmId, importViewingId,
    # shouldImportFilm. We zip items with matches positionally; if more items
    # than matches, drop the tail.
    for (data_json, per_li), (film_id, viewing_id) in zip(items, matches):
        fields.extend([
            ('importRating', per_li.get('importRating', '')),
            ('importReview', per_li.get('importReview', '')),
            ('importTags', per_li.get('importTags', '')),
            ('importWatchedDate', per_li.get('importWatchedDate', '')),
            ('importRewatch', per_li.get('importRewatch', 'false')),
            ('importFilmId', film_id),
            ('importViewingId', viewing_id),
            ('shouldImportFilm', 'true'),
        ])
    fields.extend([
        ('shouldMarkAsWatched', 'true'),
        ('shouldImportWatchedDates', 'true'),
        ('shouldImportRatings', 'true'),
    ])

    resp = session.post(
        SAVE_URL,
        data=fields,
        headers={
            'Origin': BASE,
            'Referer': IMPORT_PAGE,
        },
    )
    _check_cloudflare(resp, 'save step')
    if resp.status_code != 200:
        raise UploadError(
            f'save step failed: HTTP {resp.status_code} '
            f'(body starts: {resp.text[:200]!r})'
        )


# === public entry point ===================================================

def upload_csv_file(csv_path, cookie_string):
    """Upload an already-written Letterboxd-format CSV to the user's diary.

    Single-file upload (no chunking) -- the wizard is happy to ingest the whole
    file in one go up to its 1MB ceiling. For typical libraries that's plenty.

    Returns ``{"submitted": n, "matched": m}``. Raises ``UploadError`` on failure.
    """
    with open(csv_path, 'rb') as f:
        csv_bytes = f.read()
    if not csv_bytes.strip():
        return {'submitted': 0, 'matched': 0}

    session = _make_session(cookie_string)
    # Prime the session so cf_clearance is exercised on a GET first; some flows
    # need this to refresh edge state before the first POST. Also verifies we
    # actually reach the import wizard rather than the sign-in page.
    warm = session.get(IMPORT_PAGE, headers={'Referer': BASE + '/'})
    _check_cloudflare(warm, 'warm-up GET /import/')
    if 'standalone-flow-sign-in' in warm.text or 'screen-standalone-flow-sign-in' in warm.text:
        raise UploadError(
            "GET /import/ redirected to the sign-in page -- the cookie isn't "
            "authenticating. The most common cause is a missing or stale "
            f"{SESSION_COOKIE_PREFIX}* cookie. Re-copy the full Cookie header "
            'from a freshly-signed-in browser session.'
        )

    items = _upload_csv(session, csv_bytes)
    import_films = [data_json for data_json, _ in items]
    film_matches = _match_films(session, import_films)
    _save(session, items, film_matches)

    return {'submitted': len(items), 'matched': len(film_matches)}
