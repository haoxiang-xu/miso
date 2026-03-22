from src.service import list_catalog


class FakeStore:
    def load_items(self):
        return [
            {"id": "1", "owner": "red", "status": "active"},
            {"id": "2", "owner": "red", "status": "archived"},
            {"id": "3", "owner": "blue", "status": "active"},
        ]


def test_list_catalog_filters_by_owner_only():
    rows = list_catalog(FakeStore(), "red")
    assert [row["id"] for row in rows] == ["1", "2"]
