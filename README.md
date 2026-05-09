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

Installs the `plex2letterboxd` command in an isolated venv on your `PATH`. Works without `sudo`.

```console
$ pipx install git+https://github.com/lemehmet/plex2letterboxd.git
$ plex2letterboxd --help
```

Upgrade later with `pipx upgrade plex2letterboxd`.

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
   It must include at least `letterboxd.signed.in.as=...` and
   `com.xk72.webparts.csrf=...`.

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
- Cloudflare bot-protection occasionally challenges plain `requests` traffic.
  If you see HTML responses mentioning "Just a moment", you'll have to fall back
  to manual CSV upload.

## Author

[Max Timkovich][profile]

[import]: https://letterboxd.com/about/importing-data/
[profile]: https://letterboxd.com/djswerve/
