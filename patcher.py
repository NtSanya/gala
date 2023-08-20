from __future__ import annotations

import shutil
from copy import copy
from pathlib import Path
from typing import Mapping

from strongarm.macho import MachoParser, VirtualMemoryPointer

from configuration import JAILBREAK_ROOT, PATCHED_IMAGES_ROOT, DeviceBootConfig, IpswPatcherConfig, GalaConfig
from iPhone3_1_4_0_8A293_patches import get_iphone_3_1_4_0_8a293_patches
from os_build import ImageType, KeyRepository, OsBuildEnum
from patches import Function, Patch
from utils import TotalEnumMapping, run_and_check

_XPWNTOOL = JAILBREAK_ROOT / "tools" / "xpwn-xerub" / "ipsw-patch" / "xpwntool"
_XPWN_DMG = JAILBREAK_ROOT / "tools" / "xpwn" / "dmg" / "dmg"
_IMAGETOOL = JAILBREAK_ROOT / "tools" / "xpwn-xerub" / "ipsw-patch" / "imagetool"


class FunctionRepository:
    _BUILDS_TO_KNOWN_FUNCTIONS = TotalEnumMapping(
        {
            OsBuildEnum.iPhone3_1_4_0_8A293: [],
            OsBuildEnum.iPhone3_1_4_1_8B117: [],
            OsBuildEnum.iPhone3_1_5_0_9A334: [],
            OsBuildEnum.iPhone3_1_6_1_10B144: [
                Function(
                    name="image3_load_validate_signature",
                    address=VirtualMemoryPointer(0x8400568E),
                ),
                Function(
                    name="main_ibss",
                    address=VirtualMemoryPointer(0x840008C8),
                ),
            ],
        }
    )

    @classmethod
    def function_with_name(cls, os_build: OsBuildEnum, name: str) -> Function:
        known_functions = cls._BUILDS_TO_KNOWN_FUNCTIONS[os_build]
        names_to_functions = {f.name: f for f in known_functions}
        return names_to_functions[name]


class PatchRepository:
    @classmethod
    def builds_to_image_patches(
        cls, config: GalaConfig,
    ) -> Mapping[OsBuildEnum, Mapping[ImageType, list[Patch]]]:
        # PT: This needs to be a method, rather than a class variable, because otherwise it
        # captures file data **when the class is defined/interpreted**,
        # which is before we've rebuilt the shellcode image with new code! Annoying
        return TotalEnumMapping(
            {
                OsBuildEnum.iPhone3_1_4_0_8A293: get_iphone_3_1_4_0_8a293_patches(config),
                OsBuildEnum.iPhone3_1_4_1_8B117: ImageType.binary_types_mapping(
                    {
                        ImageType.iBSS: [],
                        ImageType.iBEC: [],
                        ImageType.KernelCache: [],
                        ImageType.RestoreRamdisk: [],
                        ImageType.RootFilesystem: [],
                    }
                ),
                OsBuildEnum.iPhone3_1_5_0_9A334: ImageType.binary_types_mapping(
                    {
                        ImageType.iBSS: [],
                        ImageType.iBEC: [],
                        ImageType.KernelCache: [],
                        ImageType.RestoreRamdisk: [],
                        ImageType.RootFilesystem: [],
                    }
                ),
                OsBuildEnum.iPhone3_1_6_1_10B144: ImageType.binary_types_mapping(
                    {
                        ImageType.iBSS: [],
                        ImageType.iBEC: [],
                        ImageType.KernelCache: [],
                        ImageType.RestoreRamdisk: [],
                        ImageType.RootFilesystem: [],
                    }
                ),
            }
        )

    @classmethod
    def patches_for_image(cls, os_build: OsBuildEnum, image: ImageType, config: GalaConfig) -> list[Patch]:
        image_patches_for_build = cls.builds_to_image_patches(config)[os_build]
        return image_patches_for_build[image]


def dump_text_section(input_file: Path) -> bytes:
    parser = MachoParser(input_file)
    binary = parser.get_armv7_slice()
    text_section = binary.section_with_name("__text", "__TEXT")
    return binary.get_content_from_virtual_address(text_section.address, text_section.size)


def decrypt_img3(path: Path, output_path: Path, key: str, iv: str):
    run_and_check(
        [
            _XPWNTOOL.as_posix(),
            path.as_posix(),
            output_path.as_posix(),
            "-k",
            key,
            "-iv",
            iv,
        ],
    )
    if not output_path.exists():
        raise RuntimeError(f"Expected decrypted img3 to be produced at {output_path.as_posix()}")


def encrypt_img3(path: Path, output_path: Path, original_img3: Path, key: str, iv: str):
    run_and_check(
        [
            _XPWNTOOL.as_posix(),
            path.as_posix(),
            output_path.as_posix(),
            "-t",
            original_img3.as_posix(),
            "-k",
            key,
            "-iv",
            iv,
        ],
    )


def apply_patches(
    patcher_config: IpswPatcherConfig,
    image_type: ImageType,
    input: Path,
    output: Path,
    patches: list[Patch],
):
    print(f"Applying {len(patches)} patches to {image_type.name}, output={output}...")
    # TODO(PT): The base address may need to vary based on OS version as well as image type?
    # TODO(PT): The base address should perhaps be renamed to something like `a_priori_load_address`
    # For Mach-O's, the MachO contains the load address. We just need to know it for objects like the iBSS and iBEC, which are 'raw'
    # For pictures and ramdisks it's irrelevant
    base_address = image_type.base_address
    input_bytes = input.read_bytes()
    patched_bytes = bytearray(copy(input_bytes))

    for patch in patches:
        patch.apply(patcher_config, input, base_address, patched_bytes)

    if patched_bytes != input_bytes:
        print(f"Bytes successfully modified?")

    output.write_bytes(patched_bytes)


def patch_decrypted_image(
    os_build: OsBuildEnum,
    image_type: ImageType,
    config: GalaConfig,
    decrypted_image_path: Path,
    patched_image_path: Path,
):
    patches = PatchRepository.patches_for_image(os_build, image_type, config)
    apply_patches(config.patcher_config, image_type, decrypted_image_path, patched_image_path, patches)


def patch_image(config: GalaConfig, image_type: ImageType) -> Path:
    patcher_config = config.patcher_config
    os_build = patcher_config.os_build
    key_pair = KeyRepository.key_iv_pair_for_image(os_build, image_type)
    image_ipsw_subpath = os_build.ipsw_path_for_image_type(image_type)
    file_name = image_ipsw_subpath.name

    ipsw = JAILBREAK_ROOT / "unzipped_ipsw" / f"{os_build.unescaped_name}_Restore.ipsw.unzipped"
    encrypted_image = ipsw / image_ipsw_subpath
    if not encrypted_image.exists():
        raise ValueError(f"Expected to find an encrypted image at {encrypted_image}")

    output_dir = PATCHED_IMAGES_ROOT / os_build.unescaped_name
    output_dir.mkdir(parents=True, exist_ok=True)
    reencrypted_image = output_dir / f"{file_name}.reencrypted"

    if image_type in ImageType.picture_types():
        # Check whether a replacement image has been specified
        if image_type in patcher_config.replacement_pictures:
            run_and_check(
                [
                    _IMAGETOOL.as_posix(),
                    "inject",
                    patcher_config.replacement_pictures[image_type].as_posix(),
                    reencrypted_image.as_posix(),
                    encrypted_image.as_posix(),
                    key_pair.iv,
                    key_pair.key,
                ],
            )
    elif image_type == ImageType.RootFilesystem:
        extracted_dmg = output_dir / f"{file_name}.extracted"
        patched_dmg = output_dir / f"{file_name}.patched"
        repacked_dmg = output_dir / f"{file_name}.repacked"

        if not patcher_config.should_rebuild_root_filesystem:
            print(f'Skip rebuilding root filesystem...')
            if not repacked_dmg.exists():
                raise ValueError(f"Supposed to skip rebuilding root filesystem, but a cached version doesn't exist")
            return repacked_dmg

        extracted_dmg.unlink(missing_ok=True)

        # Extract the root filesystem
        run_and_check([
            _XPWN_DMG.as_posix(),
            "extract",
            encrypted_image.as_posix(),
            extracted_dmg.as_posix(),
            "-k",
            key_pair.key,
        ])

        # Apply our patches
        patched_dmg.unlink(missing_ok=True)
        patch_decrypted_image(os_build, image_type, config, extracted_dmg, patched_dmg)
        print(f"Wrote patched {image_type.name} to {patched_dmg.as_posix()}")

        # Rebuild the .dmg
        repacked_dmg.unlink(missing_ok=True)
        run_and_check([
            _XPWN_DMG.as_posix(),
            "build",
            patched_dmg.as_posix(),
            repacked_dmg.as_posix(),
        ])
        print(f"Wrote repacked {image_type.name} to {repacked_dmg.as_posix()}")

    else:
        # Decrypt the image
        # (And delete any decrypted image we already produced)
        decrypted_image = output_dir / f"{file_name}.decrypted"
        decrypted_image.unlink(missing_ok=True)

        decrypt_img3(encrypted_image, decrypted_image, key_pair.key, key_pair.iv)

        patched_image = output_dir / f"{file_name}.patched"
        patched_image.unlink(missing_ok=True)
        patch_decrypted_image(os_build, image_type, config, decrypted_image, patched_image)
        print(f"Wrote patched {image_type.name} to {patched_image.as_posix()}")

        reencrypted_image = output_dir / f"{file_name}.reencrypted"
        reencrypted_image.unlink(missing_ok=True)
        encrypt_img3(patched_image, reencrypted_image, encrypted_image, key_pair.key, key_pair.iv)
        print(f"Wrote re-encrypted {image_type.name} to {reencrypted_image.as_posix()}")

    return reencrypted_image


def regenerate_patched_images(config: GalaConfig) -> Mapping[ImageType, Path]:
    return TotalEnumMapping({image_type: patch_image(config, image_type) for image_type in ImageType})


def generate_patched_ipsw(os_build: OsBuildEnum, image_types_to_paths: Mapping[ImageType, Path]) -> None:
    # Produce a patched IPSW
    ipsw = JAILBREAK_ROOT / "ipsw" / f"{os_build.unescaped_name}_Restore.ipsw.unzipped"
    output_dir = JAILBREAK_ROOT / "patched_images" / os_build.unescaped_name
    unzipped_patched_ipsw = output_dir / "patched.ipsw.unzipped"
    if unzipped_patched_ipsw.exists():
        shutil.rmtree(unzipped_patched_ipsw)
    shutil.copytree(ipsw, unzipped_patched_ipsw)
    patched_restore_ramdisk = image_types_to_paths[ImageType.RestoreRamdisk]
    restore_ramdisk_relative_path = os_build.ipsw_path_for_image_type(ImageType.RestoreRamdisk)
    restore_ramdisk_to_overwrite = unzipped_patched_ipsw / restore_ramdisk_relative_path
    print(restore_ramdisk_to_overwrite)
    restore_ramdisk_to_overwrite.write_bytes(patched_restore_ramdisk.read_bytes())

    for file in unzipped_patched_ipsw.rglob("**/*"):
        print(file)
        if file.name == ".DS_Store":
            print(f"unlinking {file}")
            file.unlink()

    # Zip it
    zipped_patched_ipsw = output_dir / "patched.ipsw.zip"
    zipped_patched_ipsw_without_extension = output_dir / "patched.ipsw"
    shutil.make_archive(zipped_patched_ipsw_without_extension.as_posix(), "zip", unzipped_patched_ipsw.as_posix())
    shutil.move(zipped_patched_ipsw, zipped_patched_ipsw_without_extension)
