//! Build script for loom-ghostty.
//!
//! When the `ffi` feature is enabled:
//! 1. Verifies that a staged libghostty.a + ghostty.h exist at
//!    `../../third_party/ghostty-build/{lib,include}`.
//! 2. Runs `bindgen` on `ghostty.h` into `$OUT_DIR/ghostty_sys.rs`.
//! 3. Emits `cargo:rustc-link-*` for the static library + required macOS
//!    system frameworks.
//!
//! Without `ffi`, this build script is a no-op; the crate compiles to a
//! pure-Rust stub that pairs with `loom-pty` for non-macOS / CI builds.

fn main() {
    println!("cargo:rerun-if-changed=build.rs");

    #[cfg(feature = "ffi")]
    ffi_build::configure();
}

#[cfg(feature = "ffi")]
mod ffi_build {
    use std::env;
    use std::path::{Path, PathBuf};

    pub fn configure() {
        let stage = stage_root();
        let include_dir = stage.join("include");
        let lib_dir = stage.join("lib");
        let header = include_dir.join("ghostty.h");
        let lib = lib_dir.join("libghostty.a");

        if !header.exists() || !lib.exists() {
            panic!(
                "\n\n libghostty not staged.\n\n \
                 Build it once with:\n \
                   cd third_party/ghostty && \\\n   TOOLCHAINS=Metal zig build -Demit-xcframework=true \\\n     -Dxcframework-target=native -Doptimize=ReleaseFast\n \
                 then stage the artifacts into third_party/ghostty-build/.\n\n \
                 Expected: {}\n            {}\n",
                header.display(),
                lib.display()
            );
        }

        println!("cargo:rerun-if-changed={}", header.display());
        println!("cargo:rerun-if-changed={}", lib.display());

        generate_bindings(&header);

        // Link the static lib.
        println!("cargo:rustc-link-search=native={}", lib_dir.display());
        println!("cargo:rustc-link-lib=static=ghostty");

        // libghostty pulls in many Apple frameworks — the big ones are
        // CoreText, CoreGraphics, Metal, AppKit, Foundation, IOKit. Plus
        // dynamic libs libc++, iconv, System (resolved automatically).
        for framework in [
            "AppKit",
            "Carbon",
            "Cocoa",
            "Metal",
            "MetalKit",
            "QuartzCore",
            "CoreGraphics",
            "CoreText",
            "CoreFoundation",
            "CoreVideo",
            "CoreServices",
            "Foundation",
            "IOKit",
            "IOSurface",
            "Security",
            "UniformTypeIdentifiers",
        ] {
            println!("cargo:rustc-link-lib=framework={framework}");
        }
        // C++ standard lib (Ghostty uses some C++ through wasmtime + others).
        println!("cargo:rustc-link-lib=c++");
    }

    fn stage_root() -> PathBuf {
        // <workspace>/crates/loom-ghostty → two up → rust → third_party/ghostty-build
        let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
        let mut p = manifest;
        p.pop(); // crates
        p.pop(); // rust  (workspace root)
        p.push("third_party");
        p.push("ghostty-build");
        p
    }

    fn generate_bindings(header: &Path) {
        let out_path = PathBuf::from(env::var("OUT_DIR").unwrap()).join("ghostty_sys.rs");
        let bindings = bindgen::Builder::default()
            .header(header.to_string_lossy().to_string())
            .clang_arg(format!("-I{}", header.parent().unwrap().display()))
            .clang_arg("-DGHOSTTY_STATIC")
            .allowlist_function("ghostty_.*")
            .allowlist_type("ghostty_.*")
            .allowlist_var("GHOSTTY_.*")
            .rustified_enum("ghostty_.*_e")
            .derive_default(true)
            .derive_debug(true)
            .layout_tests(false)
            .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
            .generate()
            .expect("bindgen failed on ghostty.h");
        bindings
            .write_to_file(&out_path)
            .expect("write ghostty_sys.rs");
    }
}
