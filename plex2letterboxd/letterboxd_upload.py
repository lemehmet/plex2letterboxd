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
import re
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

CHUNK_SIZE = 250


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


def _build_csv(entries):
    """Re-emit entries as a Letterboxd-compatible CSV byte string."""
    import csv
    import io
    buf = io.StringIO(newline='')
    w = csv.writer(buf)
    w.writerow(['Title', 'Year', 'imdbID', 'Rating10', 'WatchedDate'])
    for e in entries:
        w.writerow([e.get('title') or '',
                    e.get('year') or '',
                    e.get('imdb_id') or '',
                    e.get('rating10') or '',
                    e.get('watched_date') or ''])
    return buf.getvalue().encode('utf-8')


# === step 1: upload CSV ===================================================

_DATA_JSON_RE = re.compile(r'<li class="import-film"[^>]+data-json="([^"]+)"')
_LI_RE = re.compile(
    r'<li class="import-film"[^>]+data-json="([^"]+)"[^>]*>(.*?)</li>',
    re.S,
)


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
    for match in _LI_RE.finditer(resp.text):
        data_json = json.loads(unescape(match.group(1)))
        # Per-li hidden inputs become the form field templates per film.
        inner = match.group(2)
        per_li_fields = {}
        for fname, fval in re.findall(
                r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', inner):
            per_li_fields.setdefault(fname, fval)
        items.append((data_json, per_li_fields))

    if not items:
        raise UploadError(
            'CSV upload returned the wizard page but no import-film entries '
            'were parsed -- the import may have been rejected, or Letterboxd '
            'changed the wizard markup'
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

def upload_entries(entries, cookie_string, chunk_size=CHUNK_SIZE):
    """Upload watched-history entries to Letterboxd.

    `entries` is the list returned by `collect_entries`. `cookie_string` is the
    full Cookie header from a signed-in browser session.

    Returns ``{"submitted": n, "matched": m}``. Raises ``UploadError`` on failure.
    """
    if not entries:
        return {'submitted': 0, 'matched': 0}

    session = _make_session(cookie_string)
    # Prime the session so cf_clearance is exercised on a GET first; some flows
    # need this to refresh edge state before the first POST.
    session.get(IMPORT_PAGE, headers={'Referer': BASE + '/'})

    submitted = 0
    matched = 0
    for i in range(0, len(entries), chunk_size):
        batch = entries[i:i + chunk_size]
        submitted += len(batch)

        csv_bytes = _build_csv(batch)
        items = _upload_csv(session, csv_bytes)
        # Step 2 needs the data-json blocks parsed by Letterboxd, not our
        # in-memory entries (Letterboxd may normalize titles etc).
        import_films = [data_json for data_json, _ in items]
        film_matches = _match_films(session, import_films)
        matched += len(film_matches)

        _save(session, items, film_matches)

    return {'submitted': submitted, 'matched': matched}
