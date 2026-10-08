#!/bin/sh
# Check coexistence and upgrade with the real Debian terminfo package.
set -eu
apt-get update
set -- /packages/ghostty_*.deb
[ "$#" -eq 1 ]
package=$1
# Also cover systems that installed our first published package before
# ncurses-term: upgrading must release the alias before ncurses claims it.
previous=/previous/ghostty_1.3.1-100~debxiang1~trixie_amd64.deb
if [ -f "$previous" ]; then
    apt-get install --no-install-recommends -y "$previous"
    apt-get install --no-install-recommends -y "$package"
fi
apt-get install --no-install-recommends -y ncurses-term
alias=/usr/share/terminfo/g/ghostty
before=$(sha256sum "$alias")
dpkg-query -S "$alias"
# The previous Griffo package owns xterm-ghostty, but not the ghostty alias.
# Use the real new payload with an older version to exercise dpkg's upgrade.
dpkg-deb --raw-extract "$package" /tmp/legacy-ghostty
sed -i 's/^Version:.*/Version: 0.0.0-1/' /tmp/legacy-ghostty/DEBIAN/control
[ ! -e /tmp/legacy-ghostty/usr/share/terminfo/g/ghostty ]
[ ! -L /tmp/legacy-ghostty/usr/share/terminfo/g/ghostty ]
dpkg-deb --root-owner-group --build /tmp/legacy-ghostty /tmp/legacy-ghostty.deb
apt-get install --allow-downgrades --no-install-recommends -y /tmp/legacy-ghostty.deb
apt-get install --no-install-recommends -y "$package"
[ "$before" = "$(sha256sum "$alias")" ]
dpkg-query -S "$alias"
infocmp -x xterm-ghostty >/dev/null
ghostty +version
[ "$(dpkg-query -W -f='${Version}' ghostty)" = "$(dpkg-deb -f "$package" Version)" ]
[ -z "$(dpkg --audit)" ]
