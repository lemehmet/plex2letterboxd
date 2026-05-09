"""Upload watched-history entries to Letterboxd by impersonating the import wizard.

Letterboxd has no public API endpoint for individual developers, so this module
talks to the same internal XHR endpoints the /import/ web UI uses:

  1. POST /import/watchlist/match-import-film/   -- match titles, get film IDs
  2. POST /s/save-users-imported-imdb-history    -- commit the matched entries

Both endpoints require a valid signed-in session cookie *and* a rotating CSRF
token (cookie name `com.xk72.webparts.csrf`, submitted as form field `__csrf`).
The token is reissued on every response, so we re-read it from the cookie jar
between calls.

This is reverse-engineered from the public web UI; expect breakage if Letterboxd
changes the import flow.
"""
import json
import re
from http.cookies import SimpleCookie

import requests

BASE = 'https://letterboxd.com'
MATCH_URL = f'{BASE}/import/watchlist/match-import-film/'
SAVE_URL = f'{BASE}/s/save-users-imported-imdb-history'
CSRF_COOKIE = 'com.xk72.webparts.csrf'
SIGNED_IN_COOKIE = 'letterboxd.signed.in.as'

# Conservative chunk size; the wizard's 1MB file cap doesn't apply to the XHR
# path, but oversized batches are still risky.
CHUNK_SIZE = 250

USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
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
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain='.letterboxd.com')
    session.headers.update({
        'User-Agent': USER_AGENT,
        'Accept': 'text/html, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin': BASE,
        'Referer': f'{BASE}/import/csv/',
    })
    return session


def _csrf(session):
    token = session.cookies.get(CSRF_COOKIE, domain='.letterboxd.com')
    if not token:
        # Fall back to any-domain lookup in case requests stored it differently.
        token = session.cookies.get(CSRF_COOKIE)
    if not token:
        raise UploadError('CSRF cookie was cleared by the server (session expired?)')
    return token


def _build_match_payload(entries):
    films = []
    for e in entries:
        rating = e.get('rating10')
        try:
            rating_value = float(rating) if rating not in (None, '') else 0.0
        except (TypeError, ValueError):
            rating_value = 0.0
        try:
            year_value = int(e['year']) if e.get('year') not in (None, '') else 0
        except (TypeError, ValueError):
            year_value = 0
        films.append({
            'title': e.get('title') or '',
            'originalTitle': '',
            'rating': rating_value,
            'review': '',
            'year': year_value,
            'imdbId': e.get('imdb_id') or '',
            'letterboxdURI': '',
            'tmdbId': '',
            'tags': '',
            'watchedDate': e.get('watched_date') or '',
            'isICheckMoviesImport': False,
            'rewatch': False,
            'creators': [],
        })
    return {'importType': 'diary', 'importFilms': films}


_FILM_ID_RE = re.compile(r'name="importFilmId"\s+value="(\d+)"')


def _parse_film_ids(html):
    return _FILM_ID_RE.findall(html)


def _save_payload(film_ids, entries, csrf):
    """Build the form tuple list for /s/save-users-imported-imdb-history.

    `film_ids` and `entries` are aligned positionally; entries that didn't match
    must already have been filtered out before calling this.
    """
    data = [
        ('__csrf', csrf),
        ('filmListId', ''),
        ('name', ''),
        ('publicList', 'false'),
        ('numberedList', 'false'),
        ('notes', ''),
        ('tags', ''),
        ('shouldMarkAsWatched', 'true'),
        ('shouldImportWatchedDates', 'true'),
        ('shouldImportRatings', 'true'),
    ]
    for fid, entry in zip(film_ids, entries):
        rating = entry.get('rating10')
        data.extend([
            ('importFilmId', fid),
            ('importViewingId', ''),
            ('shouldImportFilm', 'true'),
            ('importWatchedDate', entry.get('watched_date') or ''),
            ('importRating', '' if rating in (None, '') else str(rating)),
            ('importReview', ''),
            ('importTags', ''),
            ('importRewatch', 'false'),
        ])
    return data


def _upload_chunk(session, entries):
    """Run match + save for a single chunk. Returns matched count."""
    match_resp = session.post(
        MATCH_URL,
        data={'__csrf': _csrf(session),
              'json': json.dumps(_build_match_payload(entries))},
    )
    if match_resp.status_code != 200:
        raise UploadError(
            f'match step failed: HTTP {match_resp.status_code} '
            f'(body starts: {match_resp.text[:200]!r})'
        )

    film_ids = _parse_film_ids(match_resp.text)
    if not film_ids:
        # Cookie expired or response shape changed.
        raise UploadError(
            'no films matched in this batch -- cookie may be expired, or '
            'Letterboxd changed the import response format'
        )

    # If some entries didn't match, drop their corresponding entries by index.
    # Without parsing matched-item blocks individually we can't tell *which*
    # entries failed; trust positional order and keep only the first len(film_ids).
    matched_entries = entries[:len(film_ids)]

    save_resp = session.post(
        SAVE_URL,
        data=_save_payload(film_ids, matched_entries, _csrf(session)),
    )
    if save_resp.status_code != 200:
        raise UploadError(
            f'save step failed: HTTP {save_resp.status_code} '
            f'(body starts: {save_resp.text[:200]!r})'
        )

    return len(film_ids)


def upload_entries(entries, cookie_string, chunk_size=CHUNK_SIZE):
    """Upload watched-history entries to Letterboxd.

    `entries` is the list returned by `collect_entries`. `cookie_string` is the
    full Cookie header from a signed-in browser session.

    Returns ``{"submitted": n, "matched": m}``. Raises ``UploadError`` on failure.
    """
    if not entries:
        return {'submitted': 0, 'matched': 0}

    session = _make_session(cookie_string)
    submitted = 0
    matched = 0
    for i in range(0, len(entries), chunk_size):
        batch = entries[i:i + chunk_size]
        submitted += len(batch)
        matched += _upload_chunk(session, batch)
    return {'submitted': submitted, 'matched': matched}
