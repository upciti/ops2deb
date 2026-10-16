# Small image suitable for ops2deb generate/update but not for building packages
FROM wakemeops/minideb:bullseye AS slim

# Bullseye is EOL and its packages moved to archive.debian.org. We stay on it so
# that blueprints building native code (pip wheels...) remain compatible with glibc 2.31
RUN sed -i \
    -e "s#deb.debian.org/debian#archive.debian.org/debian#" \
    -e "s#security.debian.org/debian-security#archive.debian.org/debian-security#" \
    /etc/apt/sources.list
ARG OPS2DEB_PATH="dist/ops2deb"
COPY ${OPS2DEB_PATH} /usr/local/bin/ops2deb
ENTRYPOINT ["ops2deb"]
USER 1000

# Include dependencies needed to build Debian packages
FROM slim
USER 0
RUN install_packages \
    build-essential \
    fakeroot \
    debhelper \
    binutils-arm-linux-gnueabihf \
    binutils-aarch64-linux-gnu \
    git \
    ca-certificates
USER 1000
