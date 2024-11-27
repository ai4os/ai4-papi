"""
Temporarily patch deployment creation of old modules until we have time to properly
fix/rebuild them.
"""


def patch_nextcloud_mount(
    docker_image: str,
    task: dict,
):
    """
    Some module are blocked when running deepaas.

    This happens because they run automatically rclone mounting (`mount_rclone()`).
    But when nextcloud cannot be accessed for some reason, rclone takes 5 minutes to
    return a timeout and tries it several times. So the user is not able to access
    deepaas for half an hour.

    Temporal patch:
    Set rclone timeout to 1s (instead of 5m).

    Proper fix:
    Remove automatic mounting of folders with rclone
    --> In the case of imgclas modules, we have still not implemented this because
    GPU images no longer build properly due to missing keys.

    # TODO: fix modules and remove patch
    """

    modules = [
        # imgclas + derived modules
        "DEEP-OC-image-classification-tf",
        "DEEP-OC-plants-classification-tf",
        "DEEP-OC-conus-classification-tf",
        "DEEP-OC-phytoplankton-classification-tf",
        "DEEP-OC-seeds-classification-tf",
        # other modules
        "DEEP-OC-image-classification-tf-dicom",
        "DEEP-OC-speech-to-text-tf",
    ]
    modules = [f"deephdc/{m.lower()}" for m in modules]
    # TODO: this will need to be updated to ai4os-hub

    if docker_image in modules:
        task["Env"]["RCLONE_CONTIMEOUT"] = "1s"

    return task
