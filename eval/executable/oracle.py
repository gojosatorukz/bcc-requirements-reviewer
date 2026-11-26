"""Independent acceptance calculation for the synthetic hotel card fixture, in whole KZT."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Balances:
    book: int
    hold: int

    @property
    def available(self):
        return self.book - self.hold


def reference(book, authorization, capture):
    if not 0 < capture <= authorization <= book:
        raise ValueError("Expected 0 < capture <= authorization <= book")
    common = [Balances(book, 0), Balances(book, authorization)]
    processing = [*common, Balances(book - capture, authorization - capture)]
    mobile = [*common, Balances(book - capture, 0)]
    return processing, mobile


if __name__ == "__main__":
    for capture in (17_400, 28_599, 28_600):
        ledger, mobile = reference(71_900, 28_600, capture)
        print(capture, [b.available for b in ledger], [b.available for b in mobile])
