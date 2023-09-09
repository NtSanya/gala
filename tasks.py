import sys
import time
from dataclasses import dataclass
from pathlib import Path

import shutil
from typing import Generator, Iterator, Iterable, Callable

from invoke import task
from invoke.context import Context
import requests

from gala.configuration import GALA_ROOT, DEPENDENCIES_ROOT, DEPENDENCY_PATCHES_ROOT, ZIPPED_IPSWS_ROOT
from gala.os_build import OsBuildEnum


@task
def autoformat(ctx: Context) -> None:
    path = GALA_ROOT
    print(f"\U000027A1 Running autoformat")
    ctx.run(
        f"autoflake -r --in-place --remove-all-unused-imports {path}",
        pty=True,
        echo=True,
    )
    ctx.run(f"isort {path}", pty=True, echo=True)
    ctx.run(f"black {path}", pty=True, echo=True)
    print("Finished running autoformat! \U0001F389")


def embolden(s: str) -> str:
    """Surround with ASCII control codes to request that the text be rendered in bold"""
    return f"\033[1;37m{s}\033[0m"


@dataclass
class DependencyInfo:
    repo_url: str
    # To prevent conflicts and to keep things predictable, the user must declare the name of the directory that the
    # tool should be cloned to
    cloned_directory_name: str
    git_revision: str
    # These will be executed from within the cloned directory
    compile_commands: list[str]

    # Git patch files that should be applied to the cloned tool before we attempt to compile it
    patch_files: list[Path] | None = None


def _ensure_pre_dependencies_are_installed() -> None:
    # Ensure all the pre-dependencies we need are already installed...
    pre_dependencies = [
        "git",
        "irecovery",
        "openssl",
        "rustup",
    ]
    print(f"Verifying that {embolden(str(len(pre_dependencies)))} pre-dependencies are installed...")

    for pre_dependency in pre_dependencies:
        print(f"Checking for pre-dependency \"{embolden(pre_dependency)}\"")
        if shutil.which(pre_dependency) is None:
            print(
                f"{embolden('Toolchain setup failed: ')} pre-dependency \"{embolden(pre_dependency)}\" not found. Is it installed and in $PATH?")
            sys.exit(0)

    print(embolden("Verified pre-dependencies."))
    print()


def _install_required_rust_toolchain(ctx: Context) -> None:
    print(f"Installing the Rust toolchain that supports {embolden('armv7-apple-ios')}...")
    ctx.run("rustup toolchain add nightly-2020-01-01 --profile minimal", pty=True, echo=True)
    print(f"Installed the Rust toolchain that supports {embolden('armv7-apple-ios')}...")
    print()


def _clone_and_build_dependencies(ctx: Context) -> None:
    DEPENDENCIES_ROOT.mkdir(exist_ok=True)

    dependencies = [
        DependencyInfo(
            repo_url="https://github.com/planetbeing/xpwn",
            cloned_directory_name="xpwn",
            git_revision="ac362d4ffe4d0489a26144a1483ebf3b431da899",
            patch_files=[DEPENDENCY_PATCHES_ROOT / "xpwn.patch"],
            compile_commands=[
                "cmake .",
                "make",
            ],
        ),
        DependencyInfo(
            repo_url="https://github.com/xerub/xpwn",
            cloned_directory_name="xpwn-xerub",
            git_revision="5b5ce71ea14761a53029ff41367905bc939c098c",
            patch_files=[
                DEPENDENCY_PATCHES_ROOT / "xpwn-xerub.patch",
                DEPENDENCY_PATCHES_ROOT / "xpwn-xerub2.patch",
            ],
            compile_commands=[
                "cmake .",
                "make",
            ],
        ),
        DependencyInfo(
            repo_url="https://github.com/libimobiledevice/idevicerestore",
            cloned_directory_name="idevicerestore",
            git_revision="609f7f058487596597e8e742088119fdd46729df",
            patch_files=[
                DEPENDENCY_PATCHES_ROOT / "idevicerestore.patch",
            ],
            compile_commands=[
                "./autogen.sh",
                "make",
            ],
        ),
        DependencyInfo(
            repo_url="https://github.com/kevinburke/sshpass",
            cloned_directory_name="sshpass",
            git_revision="ca7baa670d799b85ff91b4056e0a2bf9772cb2cf",
            compile_commands=[
                "./configure",
                "make",
            ],
        ),
    ]
    print(f"Building {embolden(str(len(dependencies)))} dependencies...")
    for dependency_info in dependencies:
        print(f"Cloning {embolden(dependency_info.repo_url)} to revision {embolden(dependency_info.git_revision)}...")

        with ctx.cd(DEPENDENCIES_ROOT):
            ctx.run(f"git clone {dependency_info.repo_url} {dependency_info.cloned_directory_name}")

        with ctx.cd(DEPENDENCIES_ROOT / dependency_info.cloned_directory_name):
            ctx.run(f"git checkout {dependency_info.git_revision}")

            for patch in dependency_info.patch_files or []:
                # Ensure the patch is valid and can be applied
                print(f"Validating {embolden(patch.relative_to(GALA_ROOT))}...")
                ctx.run(f"git apply --check {patch.as_posix()}")
                print(f"Applying {embolden(patch.relative_to(GALA_ROOT))}...")
                ctx.run(f"git apply {patch.as_posix()}")

            for command in dependency_info.compile_commands:
                print(f"Running compile command \"{embolden(command)}\"...")
                ctx.run(command)

        print(f"Successfully built \"{embolden(dependency_info.cloned_directory_name)}\"...")
    print()
    print(f"Successfully cloned and built {embolden(str(len(dependencies)))} dependencies.")


def _iter_bytes_received_by_chunk_size(chunk_size: int) -> Iterator[int]:
    bytes_received_so_far = 0
    while True:
        yield bytes_received_so_far
        bytes_received_so_far += chunk_size


def _download_file(url: str, dest_path: Path, percent_completed_callback: Callable[[float], None] = None) -> None:
    print(f'Downloading {embolden(url)}...')
    chunk_size = 1024 * 8
    with requests.get(url, stream=True) as resp:
        resp.raise_for_status()
        content_length = resp.raw.length_remaining
        with dest_path.open("wb") as dest_file:
            for (bytes_received_so_far, chunk) in zip(_iter_bytes_received_by_chunk_size(chunk_size),
                                                      resp.iter_content(chunk_size=chunk_size)):
                dest_file.write(chunk)

                if percent_completed_callback:
                    percent_complete = bytes_received_so_far / float(content_length)
                    percent_completed_callback(percent_complete)


def _download_file_and_report_progress(url: str, dest_path: Path) -> None:
    report_completion_at_percentage_interval = 5
    reported_percentages = []
    start_time = time.time()

    def _time_elapsed() -> str:
        return f"{int(time.time() - start_time): <3} seconds"

    def _progress_callback(percent_completed: float) -> None:
        # Scale 0.15 => 15
        percent_completed *= 100
        # Truncate 43.4 -> 40
        boundary = int(percent_completed) - (int(percent_completed) % report_completion_at_percentage_interval)
        if boundary not in reported_percentages:
            reported_percentages.append(boundary)
            print(f'{_time_elapsed()}: {embolden(f"{boundary}%")}...')

    _download_file(url, dest_path, _progress_callback)
    _progress_callback(1)


def _download_and_unzip_ipsw(ctx: Context, os_build: OsBuildEnum) -> None:
    ZIPPED_IPSWS_ROOT.mkdir(exist_ok=True)
    downloaded_path = ZIPPED_IPSWS_ROOT / f"{os_build.unescaped_name}.zip"
    _download_file_and_report_progress(os_build.download_url, downloaded_path)
    print(f'IPSW download complete.')
    print()


@task
def setup_toolchain(ctx: Context) -> None:
    print(embolden('Setting up gala toolchain...'))

    # _ensure_pre_dependencies_are_installed()
    # _install_required_rust_toolchain(ctx)
    # _clone_and_build_dependencies(ctx)
    _download_and_unzip_ipsw(ctx, OsBuildEnum.iPhone3_1_4_0_8A293)
