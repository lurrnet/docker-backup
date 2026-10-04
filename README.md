# dockback

A pull-friendly Docker Compose backup tool for hosts such as an OCI VM.

The design goal is simple:

- the Docker host prepares application-consistent backup data;
- the home NAS initiates the connection;
- the NAS pulls the prepared backup over SSH/rsync;
- the Docker host never needs network access to the NAS.

This is useful when you do **not** want to expose NAS SSH or any other NAS service to the Internet.

## Architecture

```text
                         outbound connection from home

 NAS / home network  ------------------------------------>  OCI VM
      |                                                     |
      |  1. ssh: trigger dockback backup --all              |
      |  2. rsync: pull staging data                        |
      |                                                     |
      +-- current/                                          +-- Docker Compose apps
      +-- optional restic repository                        +-- /home/ubuntu/dockback-staging
```

The OCI host does not connect back to the NAS.

## What v0.1 does

Host-side `dockback`:

- scans a root directory for Compose projects;
- asks Docker Compose for the resolved model using `docker compose config --format json`;
- detects bind mounts and named volumes;
- detects PostgreSQL, MySQL and MariaDB services from their images;
- uses logical database dumps instead of raw copies for recognized database data volumes;
- creates consistent SQLite snapshots when SQLite databases are found inside bind mounts;
- conservatively backs up unknown persistent mounts;
- skips obvious cache/tmp/log mounts;
- stores Compose files, `.env`, resolved Compose metadata, backup plan and a manifest;
- verifies the basic backup structure after creation.

NAS-side `pull-backup.sh`:

- SSHes from the NAS to the Docker host;
- triggers a fresh host-side backup;
- pulls the staging directory with rsync;
- atomically replaces the NAS-side `current` directory;
- optionally creates a local restic snapshot and applies retention;\n- only after the pull succeeds, asks the host to prune old staging snapshots.

## Important backup behavior

The default policy is intentionally conservative:

| Detected content | Default action |
| --- | --- |
| PostgreSQL data volume | logical `pg_dump -Fc` |
| MySQL/MariaDB data volume | logical dump |
| SQLite inside a bind mount | SQLite online backup snapshot + normal bind backup |
| application/config/data bind | back up |
| ordinary named volume | tar archive |
| cache/tmp/log-like mount | skip |
| unknown persistent mount | back up |

Unknown data is backed up rather than silently ignored.

### Named volumes

`dockback` resolves the effective Docker volume name from the resolved Compose model when Compose exposes it. This avoids accidentally archiving a newly-created empty volume when the actual volume is project-prefixed.

### Database consistency

Recognized PostgreSQL/MySQL/MariaDB data directories are **not** copied as ordinary volume archives. The logical dump is the authoritative backup.

Ordinary named volumes are archived while containers are running. If an unknown named volume contains an unrecognized database, its archive may only be crash-consistent. Add explicit support before relying on that for a database.

## Host installation

Requirements:

- Linux host
- Python 3.10+
- Docker Engine
- Docker Compose v2
- permission to access Docker

Clone the repository on the OCI VM and install:

```bash
git clone https://github.com/lurrnet/docker-backup.git
cd docker-backup

chmod +x host/install.sh
sudo STAGING=/home/ubuntu/dockback-staging ./host/install.sh
sudo chown -R ubuntu:ubuntu /home/ubuntu/dockback-staging
```

The installer creates:

```text
/opt/dockback/venv/
/usr/local/bin/dockback
```

### Discover Compose projects

For apps under `/home/ubuntu`:

```bash
dockback --root /home/ubuntu discover
```

Example:

```text
/home/ubuntu/coupon-watch
/home/ubuntu/smart-meter-texas
/home/ubuntu/immich
```

### Inspect before backing up

```bash
dockback inspect /home/ubuntu/coupon-watch
```

This prints the inferred database and mount backup plan without creating a backup.

### Prepare one project

```bash
dockback \
  --staging /home/ubuntu/dockback-staging \
  backup /home/ubuntu/coupon-watch
```

### Prepare all projects

```bash
dockback \
  --root /home/ubuntu \
  --staging /home/ubuntu/dockback-staging \
  backup --all
```

## Host staging layout

A prepared backup looks roughly like:

```text
/home/ubuntu/dockback-staging/
└── project-name/
    ├── LATEST
    └── 20261004T150000Z/
        ├── metadata/
        │   ├── manifest.json
        │   ├── backup-plan.json
        │   ├── compose.resolved.json
        │   └── compose/
        │       ├── compose.yaml
        │       └── .env
        ├── databases/
        │   ├── db.dump
        │   └── sqlite/
        ├── binds/
        └── volumes/
```

## NAS pull setup

The NAS needs:

- SSH client
- rsync
- optional restic

Copy:

```text
nas/pull-backup.sh
nas/dockback-pull.conf.example
```

to the NAS.

Create a config:

```bash
cp dockback-pull.conf.example dockback-pull.conf
chmod 600 dockback-pull.conf
chmod +x pull-backup.sh
```

Example:

```sh
REMOTE_HOST=your-oci-host.example.com
REMOTE_USER=ubuntu
SSH_PORT=22
SSH_KEY=/volume1/homes/backup/.ssh/id_ed25519

REMOTE_ROOT=/home/ubuntu
REMOTE_STAGING=/home/ubuntu/dockback-staging

LOCAL_ROOT=/volume1/backups/oci-docker

RESTIC_ENABLE=false
```

Then run:

```bash
./pull-backup.sh ./dockback-pull.conf
```

The NAS initiates every connection. No inbound NAS port is required.

## SSH key

Create a dedicated key on the NAS:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/dockback_oci
```

Add the public key to the OCI account used for backup access.

For the first version, the remote account must be able to:

- run `dockback`;
- read the staging directory;
- access Docker.

A later hardening step should restrict this key with a dedicated account and/or an `authorized_keys` forced command.

## Scheduling from the NAS

Example cron entry for 03:00 every night:

```cron
0 3 * * * /volume1/scripts/pull-backup.sh /volume1/scripts/dockback-pull.conf >> /volume1/backups/oci-docker/pull.log 2>&1
```

Because the schedule lives on the NAS, the OCI VM never needs credentials for the NAS. After a successful pull, the NAS calls `dockback prune` on the host and keeps the newest `REMOTE_KEEP_STAGING` snapshots per project (default: 2). If the pull or optional restic step fails, pruning is not run.

## Optional restic on the NAS

Enable in the NAS config:

```sh
RESTIC_ENABLE=true
RESTIC_REPOSITORY=/volume1/backups/restic/oci-docker
RESTIC_PASSWORD_FILE=/volume1/homes/backup/.config/restic/password

KEEP_DAILY=7
KEEP_WEEKLY=4
KEEP_MONTHLY=12
```

Initialize the repository once:

```bash
restic \
  -r /volume1/backups/restic/oci-docker \
  --password-file /volume1/homes/backup/.config/restic/password \
  init
```

This gives two layers:

```text
OCI staging
    |
    | SSH + rsync pull
    v
NAS current copy
    |
    v
encrypted/versioned restic history
```

## Secrets

The staging backup intentionally contains recovery material such as `.env` and the resolved Compose model. Those may contain passwords, tokens and API keys.

Therefore:

- protect the host staging directory;
- use SSH for transfer;
- restrict NAS backup directory permissions;
- strongly prefer encrypted restic snapshots for long-term retention;
- never commit real `.env` files to this repository.

## Tests

Pure discovery/classification tests live in `tests/`.

```bash
python -m pip install -e ".[dev]"
pytest -q
```

## Current limitations

This is an MVP, not yet a complete disaster-recovery product.

Not yet implemented:

- restore command;
- Compose-label overrides;
- MongoDB/Redis/InfluxDB-specific backup handlers;
- explicit host-side staging retention;
- notification integrations;
- NAS/controller web UI;
- full end-to-end tests against live Docker Compose fixtures.

Before depending on it for irreplaceable data, test a restore of each important application.

## Planned next steps

The most useful next additions are:

1. `dockback restore` using the manifest;
2. label-based overrides such as `io.dockback.backup.*`;
3. host staging retention/cleanup after successful NAS pulls;
4. dedicated restricted SSH pull account;
5. more database adapters;
6. backup result/status history and notifications.
