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
```

The generated CSV file can be uploaded to Letterboxd at https://letterboxd.com/import/.

## Author

[Max Timkovich][profile]

[import]: https://letterboxd.com/about/importing-data/
[profile]: https://letterboxd.com/djswerve/
