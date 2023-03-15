# Small image suitable for ops2deb generate/update but not for building packages
FROM wakemeops/minideb:bullseye AS slim
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
