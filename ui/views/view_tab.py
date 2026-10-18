"""View Images tab: the searchable visible-image gallery."""

from services.images import ImageCollection
from views.actions import GalleryActions
from views.gallery import ImageGallery
from views.shared import SearchFilter, VisiaTab


class ViewTab(VisiaTab):
    def render(self) -> None:
        collection = SearchFilter(
            self.layout,
            key="view_name_search",
            label="Search by image name",
            no_match_message="No filenames or tags match your search.",
        ).apply(ImageCollection.visible(self.api, self.files))
        if collection is not None:
            ImageGallery(self.context, GalleryActions(self.api)).render(collection)
