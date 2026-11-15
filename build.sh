#!/bin/bash
# Build Skyward recovery for vivo iQOO Neo8 (PD2301 / V2301A).
#
# Needs the toolchain rust-toolchain.toml names (installed by rustup), cargo-ndk,
# and an Android NDK. Point ANDROID_NDK_HOME at the NDK; the build then runs
# from this folder's copy of the sources.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to an Android NDK (r29 was used)}"
export ANDROID_NDK_ROOT="$ANDROID_NDK_HOME"
export PATH="$HOME/.cargo/bin:$PATH"

cd "$ROOT/upstream"

echo "== host: skyward-pack"
cargo build --release -p skyward-pack

echo "== android: skyward"
cargo ndk -t arm64-v8a --platform 30 build --release -p skyward

echo "== android: skyward-system-init (static)"
RUSTFLAGS="-C target-feature=+crt-static" \
  cargo ndk -t arm64-v8a --platform 30 build --release \
  -p skyward-system-init --target-dir target/system-init

echo "== pack: skyward-pd2301.img"
# libs 里是 PD2301 恢复环境缺少的库：VNDK APEX 里的三个 *_ndk_platform.so
# （正常开机由 /apex/com.android.vndk.v32 提供，恢复环境没有挂载），
# 以及 ssgtzd 需要的 libcurl.so（vivo 把它放在 system 分区）。
target/release/skyward-pack recovery \
  --stock "$ROOT/stock/recovery.img" \
  --binary target/aarch64-linux-android/release/skyward \
  --system-init target/system-init/aarch64-linux-android/release/system-init \
  --config "$ROOT/devices/pd2301.toml" \
  --font resources/fonts/MiSansVF.ttf \
  --mono resources/fonts/KirkMono-Regular.ttf \
  --mono-license resources/fonts/KirkMono-OFL.txt \
  --libraries "$ROOT/libs" \
  --out "$ROOT/out/skyward-pd2301.img"

ls -lh "$ROOT/out/skyward-pd2301.img"
sha256sum "$ROOT/out/skyward-pd2301.img"
