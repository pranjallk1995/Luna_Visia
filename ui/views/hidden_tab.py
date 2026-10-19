"""Hidden Images tab for the OAuth-authenticated Luna Visia user."""

from services.api import LunaVisiaAPI
from services.images import ImageCollection
from views.actions import HiddenActions
from views.gallery import ImageGallery
from views.shared import SearchFilter, VisiaTab


class HiddenTab(VisiaTab):
    def render(self) -> None:
        collection = SearchFilter(
            self.layout,
            key="hidden_name_search",
            label="Search hidden vault by image name",
            no_match_message="No hidden filenames or tags match your search.",
        ).apply(ImageCollection.from_metadata(self.files, self.api.hidden_images()))
        if collection is not None:
            ImageGallery(self.context, HiddenActions(self.api)).render(collection)
