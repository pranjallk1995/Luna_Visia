import unittest

from fakes import FakeImageRepository
from app.services.showcase import ShowcaseSorter

RED, ORANGE, BLUE, TEAL = [1.0, 0.1, 0.0], [0.9, 0.4, 0.0], [0.0, 0.1, 1.0], [0.0, 0.5, 0.8]


class ShowcaseSorterTests(unittest.TestCase):
    """Images with alike embeddings end up next to each other; newest-first comes back on request."""

    IMAGES = {  # name: embedding, oldest first
        "red_1.png": RED,
        "blue_1.png": BLUE,
        "red_2.png": ORANGE,
        "blue_2.png": TEAL,
        "red_3.png": RED,
    }

    def setUp(self) -> None:
        self.repository = FakeImageRepository()
        for name, embedding in self.IMAGES.items():
            self.add(name, embedding)
        self.sorter = ShowcaseSorter(self.repository)

    def add(self, name: str, embedding: list[float] | None) -> None:
        self.repository.rows[name] = {"name": name, "embedding": embedding, "hidden": False, "favorite": False}

    def gallery(self) -> list[str]:
        return [row["name"] for row in self.repository.list()]

    def test_similarity_groups_alike_images_from_the_newest(self) -> None:
        result = self.sorter.sort("similarity")
        order = self.gallery()
        self.assertEqual((result["image_count"], result["start"], order[0]), (5, "red_3.png", "red_3.png"))
        self.assertEqual(set(order[:3]), {"red_1.png", "red_2.png", "red_3.png"})
        self.assertEqual(set(order[3:]), {"blue_1.png", "blue_2.png"})

    def test_start_image_and_images_without_embeddings(self) -> None:
        self.add("pending.png", None)
        result = self.sorter.sort("similarity", start="blue_1.png")
        self.assertEqual(result["unranked_without_embedding"], ["pending.png"])
        self.assertEqual(self.gallery()[:3], ["pending.png", "blue_1.png", "blue_2.png"])
        with self.assertRaises(FileNotFoundError):
            self.sorter.sort("similarity", start="pending.png")

    def test_newest_clears_the_saved_order(self) -> None:
        self.sorter.sort("similarity")
        self.assertEqual(self.sorter.sort("newest")["image_count"], 5)
        self.assertEqual(self.gallery(), list(self.IMAGES))
        with self.assertRaises(ValueError):
            self.sorter.sort("random")

    def test_sorting_needs_embeddings(self) -> None:
        for row in self.repository.rows.values():
            row["embedding"] = None
        with self.assertRaisesRegex(ValueError, "backfill_metadata"):
            self.sorter.sort("similarity")

    def test_arrival_joins_its_look_alikes(self) -> None:
        self.sorter.sort("similarity")
        self.add("blue_3.png", BLUE)
        self.sorter.place("blue_3.png")
        order = self.gallery()
        self.assertEqual(set(order[3:]), {"blue_1.png", "blue_2.png", "blue_3.png"})
        self.assertEqual([self.repository.rows[name]["showcase_rank"] for name in order], [1, 2, 3, 4, 5, 6])

    def test_arrival_waits_while_newest_first_or_without_embedding(self) -> None:
        self.add("blue_3.png", BLUE)
        self.sorter.place("blue_3.png")
        self.assertTrue(all(row.get("showcase_rank") is None for row in self.repository.rows.values()))
        self.sorter.sort("similarity")
        self.add("pending.png", None)
        self.sorter.place("pending.png")
        self.assertEqual(self.gallery()[0], "pending.png")

    def test_placement_failure_never_raises(self) -> None:
        self.sorter.sort("similarity")
        self.repository.insert_showcase_rank = lambda name, rank: 1 / 0
        self.add("blue_3.png", BLUE)
        with self.assertLogs("app.services.showcase", "ERROR"):
            self.sorter.place("blue_3.png")
        self.assertEqual(self.gallery()[0], "blue_3.png")


if __name__ == "__main__":
    unittest.main()
