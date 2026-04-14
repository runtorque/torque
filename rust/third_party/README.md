# third_party

Not checked in (gitignored). Built locally on each dev machine.

## ghostty/

Ghostty source cloned from `https://github.com/ghostty-org/ghostty.git`.

## ghostty-build/

Staged build artifacts consumed by `loom-ghostty`'s `build.rs`:

```
third_party/ghostty-build/
├── include/          # headers copied from ghostty/include/
│   ├── ghostty.h
│   └── ghostty/vt/*.h
└── lib/
    └── libghostty.a  # fat static library (arm64 + x86_64), ~140 MB
```

## Rebuilding libghostty

Prerequisites:

- Full Xcode (not just Command Line Tools) — `xcrun --show-sdk-path` must print an `/Applications/Xcode.app/...` path.
- Metal Toolchain: `sudo xcodebuild -downloadComponent MetalToolchain`.
- Zig 0.15.2 matching `ghostty/build.zig.zon`'s `minimum_zig_version`.

Build + stage:

```sh
# 1. Compile libghostty (takes a few minutes; ~140 MB fat .a)
cd third_party/ghostty
TOOLCHAINS=Metal zig build \
  -Demit-xcframework=true \
  -Dxcframework-target=native \
  -Doptimize=ReleaseFast

# 2. Stage into third_party/ghostty-build/ where loom-ghostty looks.
cd ..
mkdir -p ghostty-build/lib ghostty-build/include
cp -R ghostty/include/. ghostty-build/include/
LIB=$(find ghostty/.zig-cache -name 'libghostty-fat.a' | head -1)
cp "$LIB" ghostty-build/lib/libghostty.a

# 3. Rebuild loom-ghostty with the ffi feature.
cd ../
cargo build -p loom-ghostty --features ffi
```

If the Zig build fails with `DarwinSdkNotFound`, install full Xcode (not just CLT).
If it fails with missing Metal compiler, install the Metal Toolchain component.
