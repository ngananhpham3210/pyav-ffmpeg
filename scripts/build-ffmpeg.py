import argparse
import concurrent.futures
import glob
import hashlib
import os
import platform
import shutil
import subprocess

from cibuildpkg import Builder, Package, fetch, get_platform, log_group, run, When

plat = platform.system()

def calculate_sha256(filename: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(filename, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

# --- 1. DEFINE AUDIO PACKAGES ---
# All video libraries removed. 
# Matches document 1: LAME, OGG, OPUS, VORBIS, SPEEX
# REMOVED: fdk-aac (non-free licensing issue)

audio_group = [
    Package(
        name="lame",
        source_url="http://deb.debian.org/debian/pool/main/l/lame/lame_3.100.orig.tar.gz",
        sha256="ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e",
    ),
    Package(
        name="ogg",
        source_url="http://downloads.xiph.org/releases/ogg/libogg-1.3.5.tar.gz",
        sha256="0eb4b4b9420a0f51db142ba3f9c64b333f826532dc0f48c6410ae51f4799b664",
    ),
    Package(
        name="opus",
        source_url="https://ftp.osuosl.org/pub/xiph/releases/opus/opus-1.6.tar.gz",
        sha256="b7637334527201fdfd6dd6a02e67aceffb0e5e60155bbd89175647a80301c92c",
        build_arguments=["--disable-doc", "--disable-extra-programs"],
    ),
    Package(
        name="speex",
        source_url="http://downloads.xiph.org/releases/speex/speex-1.2.1.tar.gz",
        sha256="4b44d4f2b38a370a2d98a78329fefc56a0cf93d1c1be70029217baae6628feea",
        build_arguments=["--disable-binaries"],
    ),
    Package(
        name="vorbis",
        source_url="https://ftp.osuosl.org/pub/xiph/releases/vorbis/libvorbis-1.3.7.tar.xz",
        sha256="b33cc4934322bcbf6efcbacf49e3ca01aadbea4114ec9589d1b1e9d20f72954b",
        requires=["ogg"],
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
        raise ValueError(f"sha256 mismatch for {package.name}")


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
    parser.add_argument("--community", action="store_true")

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

    available_tools = set()
    if plat == "Windows":
        available_tools.update(["gperf"]) 
        for tool in ["gcc", "g++", "curl", "gperf", "ld", "pkg-config"]:
            run(["where", tool])

    # Install build systems
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

    # --- ULTRA-MINIMAL AUDIO-ONLY FFMPEG CONFIGURATION ---
    # Aligned with document 1's audio-only build strategy
    print("=" * 50)
    print("Building ULTRA-MINIMAL AUDIO-ONLY FFmpeg")
    print("No Video | No Images | No Network")
    print("=" * 50)
    
    ffmpeg_package.build_arguments = [
        # === DISABLE PROGRAMS ===
        "--disable-ffmpeg",
        "--disable-ffplay",
        "--disable-ffprobe",
        
        # === DISABLE DOCUMENTATION ===
        "--disable-doc",
        "--disable-htmlpages",
        "--disable-manpages",
        "--disable-podpages",
        "--disable-txtpages",
        
        # === DISABLE NETWORK (NO STREAMING) ===
        "--disable-network",
        
        # === DISABLE ALL HARDWARE ACCELERATION ===
        "--disable-hwaccels",
        "--disable-cuda",
        "--disable-cuda-llvm",
        "--disable-cuvid",
        "--disable-nvenc",
        "--disable-nvdec",
        "--disable-amf",
        "--disable-audiotoolbox",
        "--disable-videotoolbox",
        "--disable-v4l2-m2m",
        "--disable-vaapi",
        "--disable-vdpau",
        "--disable-d3d11va",
        "--disable-dxva2",
        "--disable-mediacodec",
        "--disable-mmal",
        "--disable-omx",
        "--disable-rkmpp",
        
        # === DISABLE VIDEO CODECS (BUILT-IN) ===
        "--disable-decoder=h264",
        "--disable-decoder=hevc",
        "--disable-decoder=vp8",
        "--disable-decoder=vp9",
        "--disable-decoder=av1",
        "--disable-decoder=mpeg2video",
        "--disable-decoder=mpeg4",
        "--disable-decoder=msmpeg4v2",
        "--disable-decoder=msmpeg4v3",
        "--disable-encoder=h264",
        "--disable-encoder=hevc",
        "--disable-encoder=vp8",
        "--disable-encoder=vp9",
        "--disable-encoder=av1",
        "--disable-encoder=mpeg2video",
        "--disable-encoder=mpeg4",
        
        # === DISABLE ALL IMAGE FORMATS ===
        "--disable-decoder=bmp",
        "--disable-decoder=png",
        "--disable-decoder=jpeg2000",
        "--disable-decoder=mjpeg",
        "--disable-decoder=mjpegb",
        "--disable-decoder=gif",
        "--disable-decoder=tiff",
        "--disable-decoder=webp",
        "--disable-encoder=bmp",
        "--disable-encoder=png",
        "--disable-encoder=mjpeg",
        "--disable-encoder=gif",
        "--disable-encoder=tiff",
        "--disable-encoder=webp",
        
        # === DISABLE ALL, THEN ENABLE ONLY AUDIO ===
        "--disable-encoders",
        "--disable-decoders",
        "--enable-decoder=mp3*",
        "--enable-decoder=aac*",
        "--enable-decoder=opus",
        "--enable-decoder=vorbis",
        "--enable-decoder=flac",
        "--enable-decoder=alac",
        "--enable-decoder=pcm*",
        "--enable-decoder=adpcm*",
        "--enable-decoder=wavpack",
        "--enable-encoder=aac",
        "--enable-encoder=libmp3lame",
        "--enable-encoder=libopus",
        "--enable-encoder=libvorbis",
        "--enable-encoder=flac",
        "--enable-encoder=alac",
        "--enable-encoder=pcm*",
        "--enable-encoder=wavpack",
        
        # === DISABLE INPUT/OUTPUT DEVICES ===
        "--disable-indevs",
        "--disable-outdevs",
        
        # === DISABLE ALL FILTERS, ENABLE ONLY AUDIO ===
        "--disable-filters",
        "--enable-filter=aresample",
        "--enable-filter=aformat",
        "--enable-filter=anull",
        "--enable-filter=atrim",
        "--enable-filter=volume",
        "--enable-filter=pan",
        "--enable-filter=amerge",
        "--enable-filter=aconvert",
        "--enable-filter=asplit",
        "--enable-filter=channelmap",
        "--enable-filter=channelsplit",
        
        # === DISABLE EXTERNAL VIDEO LIBRARIES ===
        "--disable-libaom",
        "--disable-libdav1d",
        "--disable-libsvtav1",
        "--disable-libvpx",
        "--disable-libwebp",
        "--disable-libopenh264",
        "--disable-libx264",
        "--disable-libx265",
        "--disable-libxvid",
        "--disable-libtheora",
        "--disable-libopenjpeg",
        
        # === DISABLE MISC VIDEO/IMAGE FEATURES ===
        "--disable-libxml2",
        "--disable-lzma",
        "--disable-bzlib",
        "--disable-iconv",
        "--disable-libfreetype",
        "--disable-libfontconfig",
        "--disable-libbluray",
        "--disable-sdl2",
        "--disable-libxcb",
        "--disable-vulkan",
        "--disable-opengl",
        "--disable-opencl",
        "--disable-mediafoundation",
        
        # === DISABLE ALL PROTOCOLS, ENABLE ONLY FILE ===
        "--disable-protocols",
        "--enable-protocol=file",
        "--enable-protocol=pipe",
        
        # === DISABLE ASSEMBLY (NO NASM NEEDED) ===
        "--disable-x86asm",
        "--disable-inline-asm",
        "--disable-asm",
        
        # === ENABLE ONLY AUDIO-RELATED ===
        "--enable-gpl",
        "--enable-version3",
        "--enable-zlib",
        "--enable-libmp3lame",
        "--enable-libopus",
        "--enable-libvorbis",
        "--enable-libspeex",
        
        # === ENABLE AUDIO DEMUXERS ===
        "--enable-demuxer=mp3",
        "--enable-demuxer=ogg",
        "--enable-demuxer=flac",
        "--enable-demuxer=wav",
        "--enable-demuxer=aac",
        "--enable-demuxer=m4a",
        "--enable-demuxer=mov",
        
        # === ENABLE AUDIO MUXERS ===
        "--enable-muxer=mp3",
        "--enable-muxer=ogg",
        "--enable-muxer=flac",
        "--enable-muxer=wav",
        "--enable-muxer=adts",
        "--enable-muxer=mp4",
        
        # === ENABLE AUDIO PARSERS ===
        "--enable-parser=aac*",
        "--enable-parser=mpegaudio",
        "--enable-parser=opus",
        "--enable-parser=vorbis",
        "--enable-parser=flac",
    ]

    packages = []
    packages += audio_group
    packages += [ffmpeg_package]

    download_tars(build_tools + packages)
    
    for tool in build_tools:
        builder.build(tool, for_builder=True)
        
    for package in packages:
        builder.build(package)

    # Windows DLL Fixes
    if plat == "Windows":
        for name in (
            "avcodec", "avdevice", "avfilter", "avformat", "avutil",
            "postproc", "swresample", "swscale",
        ):
            if os.path.exists(os.path.join(dest_dir, "bin", name + ".lib")):
                shutil.move(
                    os.path.join(dest_dir, "bin", name + ".lib"),
                    os.path.join(dest_dir, "lib"),
                )
        try:
            mingw_bindir = os.path.dirname(
                subprocess.run(["where", "gcc"], check=True, stdout=subprocess.PIPE)
                .stdout.decode().splitlines()[0].strip()
            )
            for name in ("libgcc_s_seh-1.dll", "libiconv-2.dll", "libstdc++-6.dll", "libwinpthread-1.dll", "zlib1.dll"):
                src = os.path.join(mingw_bindir, name)
                if os.path.exists(src):
                    shutil.copy(src, os.path.join(dest_dir, "bin"))
        except Exception:
            pass

    # Strip Binaries
    if plat == "Darwin":
        libraries = glob.glob(os.path.join(dest_dir, "lib", "*.dylib"))
    elif plat == "Linux":
        libraries = glob.glob(os.path.join(dest_dir, "lib", "*.so"))
    elif plat == "Windows":
        libraries = glob.glob(os.path.join(dest_dir, "bin", "*.dll"))

    if libraries:
        if plat == "Darwin":
            run(["strip", "-S"] + libraries)
            try:
                run(["otool", "-L"] + libraries)
            except Exception:
                pass
        else:
            run(["strip", "-s"] + libraries)

    # Archive
    os.makedirs(output_dir, exist_ok=True)
    
    # Check what exists before archiving
    dirs_to_archive = []
    for d in ["bin", "include", "lib"]:
        if os.path.exists(os.path.join(dest_dir, d)):
            dirs_to_archive.append(d)

    run(["tar", "czvf", output_tarball, "-C", dest_dir] + dirs_to_archive)
    
    print("")
    print("=" * 50)
    print("✓ Ultra-minimal audio-only build completed!")
    print("✓ Video codecs: DISABLED")
    print("✓ Image formats: DISABLED")
    print("✓ Network protocols: DISABLED")
    print("✓ Hardware acceleration: DISABLED")
    print("")
    print("Only audio processing is available.")
    print("=" * 50)


if __name__ == "__main__":
    main()
