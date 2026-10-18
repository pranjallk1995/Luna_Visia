"""Favorites tab: the View Images gallery limited to favorite images."""

from services.images import ImageCollection
from views.actions import FavoriteActions
from views.gallery import ImageGallery
from views.shared import SearchFilter, VisiaTab


class FavoritesTab(VisiaTab):
    def render(self) -> None:
        actions = FavoriteActions(self.api)
        favorites = ImageCollection.visible(self.api, self.files).only(actions.favorite_names())
        collection = SearchFilter(
            self.layout,
            key="favorite_name_search",
            label="Search Favorites by image name",
            no_match_message="No favorite filenames or tags match your search.",
        ).apply(favorites)
        if collection is not None:
            ImageGallery(self.context, actions).render(collection)
