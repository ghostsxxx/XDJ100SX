#!/usr/bin/env bash
# Builds the xdj-sync Mixxx fork on macOS (arm64/x86_64).
#
# Expects the Mixxx fork next to this repo (../mixxx relative to XDJ100SX),
# override with MIXXX_DIR. The first run downloads the Mixxx dependency
# environment (~1.5 GB) into $MIXXX_DIR/buildenv.
#
# Usage: sync/devenv/build-mixxx.sh [--tests]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MIXXX_DIR="${MIXXX_DIR:-$(cd "$HERE/../../../mixxx" && pwd)}"
BUILD_DIR="${BUILD_DIR:-$MIXXX_DIR/build}"

BUILD_TESTS=OFF
[[ "${1:-}" == "--tests" ]] && BUILD_TESTS=ON

cd "$MIXXX_DIR"
# The buildenv script must be sourced; it exports MIXXX_VCPKG_ROOT etc.
# It is not written for `set -u`, so relax that while sourcing.
set +u
# shellcheck disable=SC1091
source tools/macos_buildenv.sh setup
set -u

cmake -S "$MIXXX_DIR" -B "$BUILD_DIR" -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_TOOLCHAIN_FILE="$MIXXX_VCPKG_ROOT/scripts/buildsystems/vcpkg.cmake" \
    -DBUILD_TESTING="$BUILD_TESTS" \
    -DQML=OFF

cmake --build "$BUILD_DIR" --parallel
echo "Built: $BUILD_DIR/mixxx"
