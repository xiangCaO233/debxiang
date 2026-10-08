FROM debian:trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 curl xz-utils dpkg-dev apt-utils gnupg minisign \
    build-essential gcc-multilib pkg-config libgtk-4-dev libadwaita-1-dev \
    libgtk4-layer-shell-dev gettext libxml2-utils libonig-dev pandoc ncurses-bin \
    libfontconfig-dev libfreetype-dev libharfbuzz-dev libpng-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
