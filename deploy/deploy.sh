#!/bin/sh
# Copy Shelf Life to the Uberspace asteroid and restart its service.
#   sh deploy/deploy.sh
# First-time setup: see "Hosting" in README.md.
set -e
HOST=uberspace-hsos
cd "$(dirname "$0")/.."

ssh "$HOST" 'mkdir -p shelf-life/shelflife/static/img'
scp -q requirements.txt "$HOST":shelf-life/
scp -q shelflife/*.py "$HOST":shelf-life/shelflife/
scp -q shelflife/static/*.html shelflife/static/*.css "$HOST":shelf-life/shelflife/static/
scp -q shelflife/static/img/*.webp "$HOST":shelf-life/shelflife/static/img/
ssh "$HOST" 'systemctl --user restart shelf-life && sleep 2 && systemctl --user is-active shelf-life'
