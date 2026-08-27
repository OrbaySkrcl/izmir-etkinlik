#!/usr/bin/env python3
"""Tekilleştirmeyi ağ olmadan gösteren demo.

Çalıştırma:  python scripts/demo.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from izmir_events.dedup.cluster import deduplicate  # noqa: E402
from izmir_events.models import RawEvent, build_event  # noqa: E402
from izmir_events.render import render_bucket  # noqa: E402
from izmir_events.util.dates import Bucket  # noqa: E402

REF = date(2026, 8, 27)
PRIORITY = {
    "kultursanat": 90, "biletix": 75, "bubilet": 70,
    "biletinial": 70, "oggusto": 40, "izmirmag": 30,
}

# Gerçek hayatta olduğu gibi: aynı etkinlikler farklı sitelerde farklı yazılmış.
SAMPLES = [
    ("bubilet", "Sezen Aksu", "27 Ağustos 2026 21:00", "Kültürpark Açıkhava Tiyatrosu", "450 TL"),
    ("biletinial", "Sezen Aksu Konseri", "27.08.2026", "Kültürpark Açıkhava", "450 TL - 1200 TL"),
    ("oggusto", "Sezen Aksu - Kültürpark Açıkhava Tiyatrosu", "27 Ağustos", None, None),
    ("izmirmag", "Efsane Sanatçı Sezen Aksu İzmir'de!", "27 Ağustos 2026", "Kültürpark", None),
    ("biletix", "SEZEN AKSU | İZMİR", "2026-08-27T21:00:00", "Kültürpark Açıkhava Tiyatrosu", "500 TL"),
    ("bubilet", "Hamlet", "28 Ağustos 2026 20:00", "İzmir Sanat", "200 TL"),
    ("biletinial", "Hamlet Makinesi", "28 Ağustos 2026 20:00", "İzmir Sanat", "180 TL"),
    ("kultursanat", "Çocuk Tiyatrosu: Uçan Balon", "28 Ağustos 2026 14:00", "İzmir Sanat", None),
    ("izmirmag", "Uçan Balon Çocuk Tiyatrosu Oyunu", "28 Ağustos 2026", "İzmir Sanat", None),
    ("kultursanat", "Modern Sanat Sergisi", "1 Ağustos - 30 Ekim 2026", "Arkas Sanat Merkezi", None),
    ("oggusto", "Modern Sanat Sergisi - Arkas", "1 Ağustos - 15 Kasım 2026", "Arkas Sanat Merkezi", None),
    ("bubilet", "Manuş Baba", "29 Ağustos 2026", "Arena İzmir", "650 TL"),
]


def main() -> None:
    events = []
    for source, title, date_text, venue, price in SAMPLES:
        event = build_event(
            RawEvent(
                source=source, title=title, date_text=date_text, venue=venue,
                price_text=price, url=f"https://{source}.example/e/{abs(hash(title)) % 9999}",
            ),
            free_by_default=(source == "kultursanat"),
            ref=REF,
        )
        if event:
            events.append(event)

    print(f"GİRDİ: {len(events)} ham kayıt (6 kaynaktan)\n")
    for event in events:
        print(f"  [{','.join(event.sources):12s}] {event.title}")

    merged, stats = deduplicate(events, source_priority=PRIORITY)

    print(f"\n{'=' * 78}\n{stats.summary()}\n{'=' * 78}\n")
    for example in stats.examples:
        print(f"  birleşti: {example}")

    print(f"\nÇIKTI: {len(merged)} benzersiz etkinlik\n")
    for event in merged:
        sources = ",".join(sorted(event.sources))
        print(
            f"  {event.start} {event.category.emoji} {event.title[:40]:40s} "
            f"{event.price.label():16s} [{sources}]"
        )

    print(f"\n{'=' * 78}\nTelegram çıktısı (/hafta):\n{'=' * 78}\n")
    for message in render_bucket(merged, Bucket.THIS_WEEK, ref=REF):
        print(message)


if __name__ == "__main__":
    main()
