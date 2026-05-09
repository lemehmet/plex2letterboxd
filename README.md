# Plex2Letterboxd

Exports watched movies from Plex to the [Letterboxd Import Format][import].

Movies are exported to a CSV file containing:
* Movie Title
* Release Year
* IMDb ID
* User Rating
* Last Watched Date

## Installation

### pipx (recommended for user-level installs)

[pipx](https://pipx.pypa.io/) installs the `plex2letterboxd` command in an
isolated venv and puts it on your `PATH`. Works on a remote host with no
`sudo` access.

CSV-export only (no upload):

```console
$ pipx install git+https://github.com/lemehmet/plex2letterboxd.git
$ plex2letterboxd --help
```

If you also want `--upload`, you need `curl_cffi` in the same venv to bypass
Cloudflare's bot challenge. Two equivalent options:

```console
# Option A: install the [upload] extra in one go
$ pipx install 'plex2letterboxd[upload] @ git+https://github.com/lemehmet/plex2letterboxd.git'

# Option B: already installed without the extra? inject curl_cffi instead
$ pipx inject plex2letterboxd curl_cffi
```

Run it the same way regardless of how you installed:

```console
$ plex2letterboxd --ini ~/.config/plex2letterboxd.ini --upload \
    --letterboxd-cookie-file ~/.letterboxd.cookie
```

Upgrade later with `pipx upgrade plex2letterboxd` (re-run the inject if you
chose Option B and pipx wipes the venv during upgrade).

### From source

```console
$ git clone https://github.com/lemehmet/plex2letterboxd.git
$ cd plex2letterboxd
$ python -m venv env
$ source env/bin/activate
$ pip install .
```

### Docker

Build the Docker image and pass a `letterboxd.csv` file into the Docker run command to store the generated CSV.

```console
$ docker build -t plex2letterboxd .
$ docker run -v $(pwd)/config.ini:/app/config.ini -v $(pwd)/letterboxd.csv:/app/letterboxd.csv plex2letterboxd
```

## Usage

Rename `config.ini.example` to `config.ini` and fill it with your Plex credentials.

```console
$ plex2letterboxd
```

(Or `python -m plex2letterboxd` if you prefer to invoke the module directly.)

```
optional arguments:
  -h, --help            show this help message and exit
  -i INI, --ini INI     config file (default: config.ini)
  -o OUTPUT, --output OUTPUT
                        file to output to (default: letterboxd.csv)
  -s SECTIONS [SECTIONS ...], --sections SECTIONS [SECTIONS ...]
                        sections to grab from (default: ['Movies'])
  -m MANAGED_USER, --managed-user MANAGED_USER
                        name of managed user to export (default: None)
  -w WATCHED_AFTER, --watched-after WATCHED_AFTER
                        only return movies watched after the given time [format:
                        YYYY-MM-DD or 30d] (default: None)
  -u, --upload          after exporting, upload the entries to Letterboxd
  --letterboxd-cookie LETTERBOXD_COOKIE
                        Letterboxd session cookie string
  --letterboxd-cookie-file LETTERBOXD_COOKIE_FILE
                        path to a file containing the Letterboxd session cookie
```

The generated CSV file can also be uploaded manually at https://letterboxd.com/import/.

## Auto-upload to Letterboxd (`--upload`)

Letterboxd's official API is partner-only and does not accept individual-developer
applications, so this tool drives the same internal endpoints the
`/import/` web UI uses. That means you must supply a **session cookie** from a
browser already signed in to letterboxd.com.

### Getting the cookie

1. Sign in to https://letterboxd.com in your browser.
2. Open the browser's devtools (`F12`) and go to the **Network** tab.
3. Reload any letterboxd.com page and click any request to letterboxd.com.
4. Under **Request Headers**, copy the entire value of the `Cookie:` header.
   The following cookies are all required:
   - `com.xk72.webparts.csrf` -- CSRF token
   - `letterboxd.signed.in.as` -- your username
   - `letterboxd.user.CURRENT` -- the actual session token; without it the
     upload silently redirects to the sign-in page
   - `cf_clearance` -- Cloudflare's bot-challenge clearance

### Providing the cookie

Pick whichever fits your setup (CLI flag wins over config file wins over env var):

```console
$ plex2letterboxd --upload --letterboxd-cookie 'com.xk72.webparts.csrf=...; letterboxd.signed.in.as=you; ...'
$ plex2letterboxd --upload --letterboxd-cookie-file ~/.letterboxd-cookie
$ LETTERBOXD_COOKIE='...' plex2letterboxd --upload
```

Or in `config.ini`:

```ini
[letterboxd]
cookie_file = ~/.letterboxd-cookie
```

### Caveats

- The cookie expires; you'll need to re-copy it periodically.
- The flow is reverse-engineered from the import wizard's XHR calls and is
  inherently fragile -- if Letterboxd changes the import page, upload will break
  before extraction does.
- letterboxd.com sits behind Cloudflare's bot challenge. Install the `[upload]`
  extra so `curl_cffi` is available to impersonate Chrome's TLS fingerprint --
  plain `requests` will hit a "Just a moment..." interstitial. If you still get
  the challenge with the extra installed, the cookie is probably stale (the
  `cf_clearance` cookie rotates frequently).

## Author

[Max Timkovich][profile]

[import]: https://letterboxd.com/about/importing-data/
[profile]: https://letterboxd.com/djswerve/
