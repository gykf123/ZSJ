#!/bin/bash
# force-armv8a-wrapper.sh
# 强制 ARMv8.0 基线（-march=armv8-a），避免 iPad 6 (A10, ARMv8.0) 因 armv8.1+ 指令
# （LSE / dotprod 等）触发 SIGILL（表现为 libdeflate_adler32 等 native 崩溃）。
#
# 做法：用包装脚本替换 clang / clang++，在每个编译命令末尾追加 -march=armv8-a。
# 由于放在命令行末尾，会覆盖 CMakeLists / 子模块可能设置的更高 -march，确保最终落到 ARMv8.0。
set -e

mkdir -p /tmp/ccwrap

cat > /tmp/ccwrap/clang <<'WRAP'
#!/bin/bash
REAL="$(xcrun -f clang 2>/dev/null || command -v clang)"
exec "$REAL" "$@" -march=armv8-a
WRAP

cat > /tmp/ccwrap/clang++ <<'WRAP'
#!/bin/bash
REAL="$(xcrun -f clang++ 2>/dev/null || command -v clang++)"
exec "$REAL" "$@" -march=armv8-a
WRAP

chmod +x /tmp/ccwrap/clang /tmp/ccwrap/clang++

export PATH="/tmp/ccwrap:$PATH"
export CC=/tmp/ccwrap/clang
export CXX=/tmp/ccwrap/clang++

# 执行调用方传入的构建命令（即 make package ...）
exec "$@"
