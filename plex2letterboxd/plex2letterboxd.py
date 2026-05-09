"""Export watched Plex movies to the Letterboxd import format."""
import argparse
import configparser
import csv
import os
import re
import sys

from plexapi.server import PlexServer

from . import letterboxd_upload


def parse_args():
    parser = argparse.ArgumentParser(
        description='Export watched Plex movies to the Letterboxd import '
                    'format.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-i', '--ini', default='config.ini',
                        help='config file')
    parser.add_argument('-o', '--output', default='letterboxd.csv',
                        help='file to output to')
    parser.add_argument('-s', '--sections', default=['Movies'], nargs='+',
                        help='sections to grab from')
    parser.add_argument('-m', '--managed-user',
                        help='name of managed user to export')
    parser.add_argument('-w', '--watched-after',
                        help='only return movies watched after the given time [format: YYYY-MM-DD or 30d]')
    parser.add_argument('-u', '--upload', action='store_true',
                        help='after exporting, upload the entries to Letterboxd '
                             '(requires a session cookie; see README)')
    parser.add_argument('--letterboxd-cookie',
                        help='Letterboxd session cookie string. '
                             'Overrides config.ini and the LETTERBOXD_COOKIE env var')
    parser.add_argument('--letterboxd-cookie-file',
                        help='path to a file containing the Letterboxd session cookie string')
    return parser.parse_args()


def parse_config(ini):
    """Read and validate config file."""
    config = configparser.ConfigParser()
    config.read(ini)
    if 'auth' not in config:
        print(f"Missing [auth] section in {ini}")
        sys.exit(1)
    auth = config['auth']
    missing = {'baseurl', 'token'} - set(auth.keys())
    if missing:
        print(f'Missing the following config values: {missing}')
        sys.exit(1)
    return config


def resolve_letterboxd_cookie(args, config):
    """Resolve the Letterboxd cookie from CLI args, config, or env, in that order."""
    if args.letterboxd_cookie:
        return args.letterboxd_cookie
    if args.letterboxd_cookie_file:
        with open(args.letterboxd_cookie_file, encoding='utf-8') as f:
            return f.read().strip()
    if 'letterboxd' in config and config['letterboxd'].get('cookie'):
        return config['letterboxd']['cookie'].strip()
    if 'letterboxd' in config and config['letterboxd'].get('cookie_file'):
        path = os.path.expanduser(config['letterboxd']['cookie_file'])
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    env = os.environ.get('LETTERBOXD_COOKIE')
    if env:
        return env.strip()
    return None


def getImdbId(movie):
    for guid in (g.id for g in movie.guids):
        if guid.startswith('imdb'):
            return re.sub('^imdb://', '', guid)
    return None


def collect_entries(sections, watched_after=None):
    """Iterate watched movies in `sections` and return Letterboxd-shaped dicts."""
    entries = []
    for section in sections:
        filters = {'unwatched': False}
        if watched_after:
            filters['lastViewedAt>>'] = watched_after
        for movie in section.search(sort='lastViewedAt', filters=filters):
            date = None
            if movie.lastViewedAt is not None:
                date = movie.lastViewedAt.strftime('%Y-%m-%d')
            rating = movie.userRating
            if rating is not None:
                rating = f'{rating:.0f}'
            entries.append({
                'title': movie.title,
                'year': movie.year,
                'imdb_id': getImdbId(movie),
                'rating10': rating,
                'watched_date': date,
            })
    return entries


def write_csv(entries, output):
    """Write entries to a Letterboxd import CSV."""
    with open(output, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Title', 'Year', 'imdbID', 'Rating10', 'WatchedDate'])
        for e in entries:
            writer.writerow([e['title'], e['year'], e['imdb_id'],
                             e['rating10'], e['watched_date']])
    print(f'Exported {len(entries)} movies to {output}.')


def main():
    args = parse_args()
    config = parse_config(args.ini)
    auth = config['auth']

    plex = PlexServer(auth['baseurl'], auth['token'])
    if args.managed_user:
        myplex = plex.myPlexAccount()
        user = myplex.user(args.managed_user)
        # Get the token for your machine.
        token = user.get_token(plex.machineIdentifier)
        # Login to your server using your friend's credentials.
        plex = PlexServer(auth['baseurl'], token)

    sections = [plex.library.section(s) for s in args.sections]
    entries = collect_entries(sections, args.watched_after)
    write_csv(entries, args.output)

    if args.upload:
        cookie = resolve_letterboxd_cookie(args, config)
        if not cookie:
            print('--upload was requested but no Letterboxd cookie was provided. '
                  'Pass --letterboxd-cookie, set LETTERBOXD_COOKIE, or add a '
                  '[letterboxd] cookie entry to config.ini.', file=sys.stderr)
            sys.exit(2)
        try:
            result = letterboxd_upload.upload_entries(entries, cookie)
        except letterboxd_upload.UploadError as e:
            print(f'Letterboxd upload failed: {e}', file=sys.stderr)
            sys.exit(3)
        print(f"Uploaded to Letterboxd: matched {result['matched']} of "
              f"{result['submitted']} submitted entries.")
