from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Listing:
    id: str
    title: str
    price: str
    price_amount: float | None
    location: str
    url: str
    thumbnail: str | None = None
    posted: str = "Not shown by Facebook"
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
