#!/usr/bin/env bash
set -euo pipefail

package_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
build_dir=${1:-/disk/dev/osm-map-cpp-build}
install_dir=${2:-"$package_dir"}
include_dir=/usr/include

if [[ ! -f "$include_dir/osmium/io/reader.hpp" ]]; then
 deps_dir="$build_dir/deps"
 include_dir="$deps_dir/root/usr/include"
 if [[ ! -f "$include_dir/osmium/io/reader.hpp" ]]; then
 mkdir -p "$deps_dir/packages" "$deps_dir/root"
 (
 cd "$deps_dir/packages"
 apt-get download libosmium2-dev libprotozero-dev
 for package_file in ./*.deb; do
 dpkg-deb -x "$package_file" "$deps_dir/root"
 done
 )
 fi
fi

cmake -S "$package_dir/cpp" -B "$build_dir" \
 -DCMAKE_BUILD_TYPE=Release \
 -DOSMIUM_INCLUDE_DIR="$include_dir" \
 -DCMAKE_INSTALL_PREFIX="$install_dir"
cmake --build "$build_dir" --parallel
cmake --install "$build_dir"

echo "builder=$install_dir/bin/osm_map_builder"
