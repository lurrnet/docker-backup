#!/bin/sh
set -eu

PREFIX="${PREFIX:-/opt/dockback}"
STAGING="${STAGING:-/home/ubuntu/dockback-staging}"

python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip
"$PREFIX/venv/bin/pip" install .

mkdir -p "$STAGING"

cat > /usr/local/bin/dockback <<EOF
#!/bin/sh
exec "$PREFIX/venv/bin/dockback" "\$@"
EOF
chmod 0755 /usr/local/bin/dockback

echo "dockback installed at /usr/local/bin/dockback"
echo "staging directory: $STAGING"
