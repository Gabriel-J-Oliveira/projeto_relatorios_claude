from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class AnexosStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.ANEXOS_ROOT)
        kwargs.setdefault("base_url", settings.ANEXOS_URL)
        super().__init__(*args, **kwargs)


anexos_storage = AnexosStorage()


@deconstructible
class HelpImagesStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.HELP_IMAGES_ROOT)
        kwargs.setdefault("base_url", None)
        super().__init__(*args, **kwargs)


help_images_storage = HelpImagesStorage()
