import argparse
import concurrent.futures
import glob
import hashlib
import os
import platform
import shutil
import subprocess

# Ensure cibuildpkg is installed/available in your environment
from cibuildpkg import Builder, Package, fetch, get_platform, log_group, run

plat = platform.system()

def calculate_sha256(filename: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(filename, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

# --- 1. MINIMAL DEPENDENCIES ---

# We only keep OPUS. Your miner relies heavily on 'opus' inside 'webm'.
codec_group = [
    Package(
        name="opus",
        source_url="https://ftp.osuosl.org/pub/xiph/releases/opus/opus-1.6.tar.gz",
        sha256="b7637334527201fdfd6dd6a02e67aceffb0e5e60155bbd89175647a80301c92c",
        build_arguments=["--disable-doc", "--disable-extra-programs"],
    ),
]

ffmpeg_package = Package(
    name="ffmpeg",
    source_url="https://ffmpeg.org/releases/ffmpeg-8.0.tar.xz",
    sha256="b2751fccb6cc4c77708113cd78b561059b6fa904b24162fa0be2d60273d27b8e",
    build_arguments=[],
    build_parallel=plat != "Windows",
)


def download_and_verify_package(package: Package) -> None:
    tarball = os.path.join(
        os.path.abspath("source"),
        package.source_filename or package.source_url.split("/")[-1],
    )

    if not os.path.exists(tarball):
        try:
            fetch(package.source_url, tarball)
        except subprocess.CalledProcessError:
            pass

    if not os.path.exists(tarball):
        raise ValueError(f"tar bar doesn't exist: {tarball}")

    sha = calculate_sha256(tarball)
    if package.sha256 == sha:
        print(f"{package.name} tarball: hashes match")
    else:
        raise ValueError(
            f"sha256 hash of {package.name} tarball do not match!\nExpected: {package.sha256}\nGot: {sha}"
        )


def download_tars(packages: list[Package]) -> None:
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_package = {
            executor.submit(download_and_verify_package, package): package.name
            for package in packages
        }

        for future in concurrent.futures.as_completed(future_to_package):
            name = future_to_package[future]
            try:
                future.result()
            except Exception as exc:
                print(f"{name} generated an exception: {exc}")
                raise


def main():
    parser = argparse.ArgumentParser("build-ffmpeg")
    parser.add_argument("destination")
    parser.add_argument("--community", action="store_true") # Retained for arg compatibility

    args = parser.parse_args()

    dest_dir = os.path.abspath(args.destination)

    output_dir = os.path.abspath("output")
    if plat == "Linux" and os.environ.get("CIBUILDWHEEL") == "1":
        output_dir = "/output"
    output_tarball = os.path.join(output_dir, f"ffmpeg-{get_platform()}.tar.gz")

    if os.path.exists(output_tarball):
        return

    builder = Builder(dest_dir=dest_dir)
    builder.create_directories()

    # --- 2. TOOLS ---
    available_tools = set()
    if plat == "Windows":
        available_tools.update(["gperf"]) 
        print("PATH", os.environ["PATH"])
        for tool in ["gcc", "g++", "curl", "gperf", "ld", "pkg-config"]:
            run(["where", tool])

    # Standard python tools
    with log_group("install python packages"):
        run(["pip", "install", "cmake==3.31.6", "meson", "ninja"])
    
    build_tools = []
    if "gperf" not in available_tools:
        build_tools.append(
            Package(
                name="gperf",
                source_url="http://ftp.gnu.org/pub/gnu/gperf/gperf-3.1.tar.gz",
                sha256="588546b945bba4b70b6a3a616e80b4ab466e3f33024a352fc2198112cdbb3ae2",
            )
        )

    # --- 3. FFMPEG CONFIGURATION ---
    ffmpeg_package.build_arguments = [
        "--disable-programs",      # You use PyAV, not ffmpeg.exe
        "--disable-doc",
        "--disable-libxml2",
        "--disable-lzma",
        "--disable-libtheora",
        "--disable-libfreetype",
        "--disable-libfontconfig",
        "--disable-libbluray",
        "--disable-libopenjpeg",
        "--disable-mediafoundation",
        
        # --- CRITICAL FIX FOR YOUR ERROR ---
        "--disable-x86asm",        # Disable Assembly optimizations. Fixes "nasm not found".
        # -----------------------------------

        # Audio / Network / Device disabling
        "--disable-alsa",          # No playback
        "--disable-gnutls",        # No HTTPS (Python handles it)
        "--disable-libxcb",        # No screen cap
        "--disable-sdl2",
        "--disable-vulkan",
        "--disable-cuda",
        "--disable-cuvid",
        "--disable-nvenc",
        "--disable-nvdec",
        "--disable-amf",
        "--disable-audiotoolbox",
        "--disable-videotoolbox",

        # Disable external libraries we removed
        "--disable-libaom",
        "--disable-libdav1d",
        "--disable-libmp3lame",
        "--disable-libopencore-amrnb",
        "--disable-libopencore-amrwb",
        "--disable-libspeex",
        "--disable-libsvtav1",
        "--disable-libtwolame",
        "--disable-libvorbis",
        "--disable-libvpx",
        "--disable-libwebp",
        "--disable-libopenh264",
        "--disable-libx264",
        "--disable-libx265",
        
        # Enable essentials
        "--enable-version3",
        "--enable-zlib",           # Needed for MP4/MKV container structure
        "--enable-libopus",        # Keep for stability
    ]

    # Clean list of packages to build
    packages = []
    packages += codec_group
    packages += [ffmpeg_package]

    download_tars(build_tools + packages)
    
    for tool in build_tools:
        builder.build(tool, for_builder=True)
        
    for package in packages:
        builder.build(package)

    # --- 4. WINDOWS POST-PROCESSING ---
    if plat == "Windows":
        # fix .lib files being installed in the wrong directory
        for name in (
            "avcodec",
            "avdevice",
            "avfilter",
            "avformat",
            "avutil",
            "postproc",
            "swresample",
            "swscale",
        ):
            if os.path.exists(os.path.join(dest_dir, "bin", name + ".lib")):
                shutil.move(
                    os.path.join(dest_dir, "bin", name + ".lib"),
                    os.path.join(dest_dir, "lib"),
                )

        # copy some libraries provided by mingw (e.g. zlib, libgcc)
        try:
            mingw_bindir = os.path.dirname(
                subprocess.run(["where", "gcc"], check=True, stdout=subprocess.PIPE)
                .stdout.decode()
                .splitlines()[0]
                .strip()
            )
            for name in (
                "libgcc_s_seh-1.dll",
                "libiconv-2.dll",
                "libstdc++-6.dll",
                "libwinpthread-1.dll",
                "zlib1.dll",
            ):
                src = os.path.join(mingw_bindir, name)
                if os.path.exists(src):
                    shutil.copy(src, os.path.join(dest_dir, "bin"))
        except Exception as e:
            print(f"Warning: Could not copy MinGW DLLs: {e}")

    # --- 5. FINALIZE ---
    # find libraries
    if plat == "Darwin":
        libraries = glob.glob(os.path.join(dest_dir, "lib", "*.dylib"))
    elif plat == "Linux":
        libraries = glob.glob(os.path.join(dest_dir, "lib", "*.so"))
    elif plat == "Windows":
        libraries = glob.glob(os.path.join(dest_dir, "bin", "*.dll"))

    # strip libraries
    if libraries:
        if plat == "Darwin":
            run(["strip", "-S"] + libraries)
            try:
                run(["otool", "-L"] + libraries)
            except Exception:
                pass
        else:
            run(["strip", "-s"] + libraries)

    # build output tarball
    os.makedirs(output_dir, exist_ok=True)
    run(["tar", "czvf", output_tarball, "-C", dest_dir, "bin", "include", "lib"])


if __name__ == "__main__":
    main()
