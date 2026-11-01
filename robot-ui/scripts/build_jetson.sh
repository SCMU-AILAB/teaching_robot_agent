#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CMAKE_ARGS=(-S "$ROOT" -B "$ROOT/build" -DCMAKE_BUILD_TYPE=Release)
REQUIRED_HEADERS=(
    /usr/include/X11/extensions/Xrandr.h
    /usr/include/X11/extensions/XInput2.h
    /usr/include/X11/Xcursor/Xcursor.h
    /usr/include/X11/extensions/Xinerama.h
    /usr/include/X11/extensions/Xfixes.h
    /usr/include/X11/extensions/Xrender.h
)

missing=0
for header in "${REQUIRED_HEADERS[@]}"; do
    if [[ ! -f "$header" ]]; then
        missing=1
    fi
done

if [[ "$missing" -eq 1 ]]; then
    echo "未找到 X11 开发头文件，将在 robot-ui/.deps 内下载并解包（不需要 sudo）。"
    mkdir -p "$ROOT/.deps/debs" "$ROOT/.deps/sysroot"
    (
        cd "$ROOT/.deps/debs"
        apt-get download \
            libxrandr-dev libxi-dev libxcursor-dev libxinerama-dev \
            libxfixes-dev libxrender-dev libxext-dev x11proto-dev
    )
    for package in "$ROOT"/.deps/debs/*.deb; do
        dpkg-deb -x "$package" "$ROOT/.deps/sysroot"
    done
    CMAKE_ARGS+=(
        -DCMAKE_INCLUDE_PATH="$ROOT/.deps/sysroot/usr/include"
        -DCMAKE_LIBRARY_PATH="$ROOT/.deps/sysroot/usr/lib/aarch64-linux-gnu"
        -DCMAKE_PREFIX_PATH="$ROOT/.deps/sysroot/usr"
    )
fi

cmake "${CMAKE_ARGS[@]}"
cmake --build "$ROOT/build" -j"$(nproc)"
ctest --test-dir "$ROOT/build" --output-on-failure
echo "构建完成：$ROOT/build/robot_ui"
