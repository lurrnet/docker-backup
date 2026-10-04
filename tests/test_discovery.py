from dockback.discovery import classify_mount, detect_database


def test_detect_postgres():
    service = {
        "image": "postgres:17",
        "environment": {
            "POSTGRES_DB": "app",
            "POSTGRES_USER": "appuser",
            "POSTGRES_PASSWORD": "secret",
        },
    }
    db = detect_database("db", service)
    assert db is not None
    assert db.kind == "postgres"
    assert db.database == "app"
    assert db.user == "appuser"


def test_detect_mariadb():
    service = {
        "image": "mariadb:11",
        "environment": {"MARIADB_DATABASE": "app", "MARIADB_USER": "u"},
    }
    db = detect_database("db", service)
    assert db is not None
    assert db.kind == "mariadb"


def test_database_volume_uses_logical_dump():
    db = detect_database("db", {"image": "postgres:17"})
    mount = {
        "type": "volume",
        "source": "postgres_data",
        "target": "/var/lib/postgresql/data",
    }
    spec = classify_mount("db", mount, db)
    assert spec.category == "database"
    assert spec.action == "logical-dump"


def test_unknown_volume_is_backed_up():
    mount = {
        "type": "volume",
        "source": "mystery",
        "target": "/opt/app/state",
    }
    spec = classify_mount("app", mount, None)
    assert spec.category == "unknown"
    assert spec.action == "backup"


def test_cache_mount_is_skipped():
    mount = {
        "type": "bind",
        "source": "./cache",
        "target": "/app/cache",
    }
    spec = classify_mount("app", mount, None)
    assert spec.category == "cache"
    assert spec.action == "skip"
