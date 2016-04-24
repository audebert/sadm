#!/bin/bash


SADM_FGP=F4592F5F00D9EA8279AE25190312438E8809C743

PKG=${1?Usage: $0 package}

set -e

echo "[+] Checking gpg key store"

if ! gpg --list-secret-keys $SADM_FGP >/dev/null 2>&1; then
    cat >&2 <<EOF
GPG key store is missing the prologin private key
To get it:

    $ ssh repo@prologin.org 'gpg --export-secret-keys --armor $SADM_FGP' | gpg --import
EOF
    exit 1
fi

echo "[+] Signing the package"
gpg --sign --local-user $SADM_FGP --detach-sign --output $PKG.sig $PKG
echo "[+] Retrieving the database"
rsync -Pha repo@prologin.org:www/{prologin.db,prologin.db.sig,prologin.db.tar.gz,prologin.db.tar.gz.sig,prologin.pub} .
echo "[+] Adding the package to the database"
repo-add --sign --verify --key $SADM_FGP prologin.db.tar.gz $PKG
echo "[+] Uploading the package and the database"
rsync -Pha $PKG $PKG.sig prologin.{db,db.sig,db.tar.gz,db.tar.gz.sig,pub} repo@prologin.org:www/