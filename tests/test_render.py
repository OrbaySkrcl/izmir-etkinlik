"""Telegram mesaj biçimlendirme testleri."""

import re

from izmir_events.render import (
    SAFE_LIMIT,
    TELEGRAM_LIMIT,
    esc,
    event_line,
    render_bucket,
    render_digest,
    render_event_detail,
    render_events,
    render_stats,
)
from izmir_events.util.dates import Bucket


class TestEscaping:
    def test_html_special_chars_escaped(self):
        assert esc("Rock & Roll <br>") == "Rock &amp; Roll &lt;br&gt;"

    def test_title_with_ampersand_is_safe(self, event_factory, ref):
        event = event_factory("bubilet", "Rock & Roll Gecesi", "12 Eylül 2026", "Arena")
        line = event_line(event, ref=ref)
        assert "&amp;" in line
        assert "&<" not in line


class TestEventLine:
    def test_links_title_when_url_present(self, event_factory, ref):
        event = event_factory("bubilet", "Bir Konser", "12 Eylül 2026", url="https://x.test/1")
        assert '<a href="https://x.test/1">' in event_line(event, ref=ref)

    def test_bold_title_without_url(self, event_factory, ref):
        event = event_factory("bubilet", "Bir Konser", "12 Eylül 2026")
        event.sources = {"bubilet": ""}
        assert "<b>Bir Konser</b>" in event_line(event, ref=ref)

    def test_free_marked(self, event_factory, ref):
        event = event_factory(
            "kultursanat", "Ücretsiz Etkinlik", "12 Eylül 2026", price_text="Ücretsiz"
        )
        assert "Ücretsiz" in event_line(event, ref=ref)

    def test_multi_source_badge(self, event_factory, ref):
        event = event_factory("bubilet", "Konser", "12 Eylül 2026")
        event.sources["oggusto"] = "https://y.test/2"
        assert "2 kaynak" in event_line(event, ref=ref)

    def test_single_source_has_no_badge(self, event_factory, ref):
        event = event_factory("bubilet", "Konser", "12 Eylül 2026")
        assert "kaynak" not in event_line(event, ref=ref)


class TestRenderEvents:
    def test_empty_list_gives_friendly_message(self, ref):
        messages = render_events([], title="Bugün", ref=ref, empty_message="Hiç yok.")
        assert len(messages) == 1
        assert "Hiç yok." in messages[0]

    def test_groups_by_day(self, event_factory, ref):
        events = [
            event_factory("bubilet", "Bugünkü", "27 Ağustos 2026"),
            event_factory("bubilet", "Yarınki", "28 Ağustos 2026"),
        ]
        text = "\n".join(render_events(events, title="Liste", ref=ref))
        assert "Bugün —" in text
        assert "Yarın —" in text

    def test_splits_long_lists_into_multiple_messages(self, event_factory, ref):
        events = [
            event_factory(
                "bubilet",
                f"Çok Uzun Etkinlik Adı Numara {i}",
                "12 Eylül 2026",
                f"Bir Mekan Adı {i}",
            )
            for i in range(120)
        ]
        messages = render_events(events, title="Liste", ref=ref)
        assert len(messages) > 1
        assert all(len(m) <= TELEGRAM_LIMIT for m in messages)
        assert all(len(m) <= SAFE_LIMIT + 200 for m in messages)

    def test_continuation_header_present(self, event_factory, ref):
        events = [
            event_factory("bubilet", f"Etkinlik {i}", f"{(i % 28) + 1} Eylül 2026")
            for i in range(150)
        ]
        messages = render_events(events, title="Liste", ref=ref)
        assert "devam" in messages[1]

    def test_hundreds_on_one_day_still_within_limit(self, event_factory, ref):
        # Tek güne yığılmış çok sayıda etkinlikte de her mesaj sınırı aşmamalı.
        events = [
            event_factory("bubilet", f"Etkinlik {i}", "12 Eylül 2026", "Mekan") for i in range(500)
        ]
        messages = render_events(events, title="Liste", ref=ref)
        assert len(messages) > 5
        assert all(len(m) <= TELEGRAM_LIMIT for m in messages)

    def test_day_heading_repeated_on_continuation(self, event_factory, ref):
        events = [
            event_factory("bubilet", f"Etkinlik {i}", "12 Eylül 2026", "Mekan") for i in range(200)
        ]
        messages = render_events(events, title="Liste", ref=ref)
        assert "12 Eylül" in messages[1]

    def test_absurdly_long_title_truncated(self, event_factory, ref):
        events = [event_factory("bubilet", "A" * 5000, "12 Eylül 2026")]
        messages = render_events(events, title="Liste", ref=ref)
        assert all(len(m) <= TELEGRAM_LIMIT for m in messages)

    def test_numbered_mode(self, event_factory, ref):
        events = [event_factory("bubilet", "Bir", "12 Eylül 2026")]
        text = render_events(events, title="Arama", ref=ref, group_by_day=False, numbered=True)[0]
        assert re.search(r"1\.", text)

    def test_footer_appended(self, event_factory, ref):
        events = [event_factory("bubilet", "Bir", "12 Eylül 2026")]
        messages = render_events(events, title="Liste", ref=ref, footer="Alt not")
        assert "Alt not" in messages[-1]


class TestBucketAndDigest:
    def test_bucket_title(self, event_factory, ref):
        events = [event_factory("bubilet", "Bir", "27 Ağustos 2026")]
        assert "Bugün" in render_bucket(events, Bucket.TODAY, ref=ref)[0]

    def test_free_only_title(self, ref):
        text = render_bucket([], Bucket.TODAY, ref=ref, free_only=True)[0]
        assert "Ücretsiz" in text

    def test_digest_greeting(self, event_factory, ref):
        events = [event_factory("bubilet", "Bir", "27 Ağustos 2026")]
        text = render_digest(events, ref=ref, free_count=2)[0]
        assert "Günaydın" in text
        assert "2 etkinlik ücretsiz" in text

    def test_empty_digest_suggests_tomorrow(self, ref):
        assert "/yarin" in render_digest([], ref=ref)[0]


class TestDetail:
    def test_shows_all_sources(self, event_factory, ref):
        event = event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark")
        event.sources["oggusto"] = "https://y.test/2"
        event.source_titles["oggusto"] = "Sezen Aksu Konseri"
        text = render_event_detail(event, ref=ref)
        assert "bubilet" in text
        assert "oggusto" in text

    def test_shows_alternate_titles(self, event_factory, ref):
        event = event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026")
        event.source_titles["oggusto"] = "Sezen Aksu Konseri"
        text = render_event_detail(event, ref=ref)
        assert "Sezen Aksu Konseri" in text


class TestStats:
    def test_renders_counts(self):
        text = render_stats(
            {"gelecek": 42, "ucretsiz": 10, "cok_kaynakli": 5, "toplam": 60},
            {"konser": 20},
            "Son tarama: dün",
        )
        assert "42" in text
        assert "Konser" in text
        assert "Son tarama" in text

    def test_unknown_category_key_does_not_crash(self):
        assert "bilinmeyen" in render_stats({}, {"bilinmeyen": 3})
