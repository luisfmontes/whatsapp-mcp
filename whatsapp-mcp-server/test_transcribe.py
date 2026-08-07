"""Stdlib unittest for the two pure functions whose silent regression is worst:
_is_expired (false-positive => permanent data loss) and _strip_accents (search
misses). Run: python3 -m unittest test_transcribe -v"""

import unittest
from datetime import datetime, timedelta, timezone

from transcribe import _is_expired, _parse_db_ts, _retry_after_seconds, CDN_EXPIRY
from whatsapp import _strip_accents


def _iso(delta_days):
    dt = datetime.now(timezone.utc) - timedelta(days=delta_days)
    return dt.isoformat()


def _go_string(delta_days, tz_hours=-3):
    """The same instant spelled the way modernc.org/sqlite (Windows) stores it:
    Go's time.Time.String() — compact offset plus a trailing zone name."""
    tz = timezone(timedelta(hours=tz_hours))
    dt = (datetime.now(timezone.utc) - timedelta(days=delta_days)).astimezone(tz)
    sign = "+" if tz_hours >= 0 else "-"
    hh = abs(tz_hours)
    return f"{dt:%Y-%m-%d %H:%M:%S} {sign}{hh:02d}00 {sign}{hh:02d}"


class IsExpiredTest(unittest.TestCase):
    def test_recent_is_not_expired(self):
        self.assertFalse(_is_expired(_iso(0)))
        self.assertFalse(_is_expired(_iso(CDN_EXPIRY.days - 1)))

    def test_old_is_expired(self):
        self.assertTrue(_is_expired(_iso(CDN_EXPIRY.days + 5)))

    def test_unknown_age_assumed_expired(self):
        # None / unparseable must NOT leave a row retried forever.
        self.assertTrue(_is_expired(None))
        self.assertTrue(_is_expired(""))
        self.assertTrue(_is_expired("not-a-date"))

    def test_naive_timestamp_handled(self):
        # go-sqlite3 stores an offset, but a naive string must not crash.
        naive = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
        self.assertFalse(_is_expired(naive.isoformat()))

    def test_go_time_string_layout_is_not_treated_as_expired(self):
        # The Windows regression: every row in messages.db is spelled this way,
        # so a parse failure here marked the whole backlog permanently
        # unavailable instead of retrying it.
        self.assertFalse(_is_expired(_go_string(0)))
        self.assertFalse(_is_expired(_go_string(CDN_EXPIRY.days - 1)))
        self.assertTrue(_is_expired(_go_string(CDN_EXPIRY.days + 5)))


class ParseDbTsTest(unittest.TestCase):
    def test_go_time_string_offset_and_zone_name(self):
        dt = _parse_db_ts("2026-08-07 10:07:53 -0300 -03")
        self.assertEqual(dt.utcoffset(), timedelta(hours=-3))
        self.assertEqual(
            dt.replace(tzinfo=None), datetime(2026, 8, 7, 10, 7, 53))

    def test_go_time_string_utc_and_fractional_seconds(self):
        dt = _parse_db_ts("2026-08-07 13:07:53.123456789 +0000 UTC")
        self.assertEqual(dt.utcoffset(), timedelta(0))
        self.assertEqual(dt.microsecond, 123456)

    def test_cgo_and_rfc3339_layouts_still_parse(self):
        # macOS/Linux (mattn) spelling, and what the bridge emits over HTTP.
        for text in ("2026-08-07 10:07:53-03:00", "2026-08-07T13:07:53+00:00"):
            self.assertIsNotNone(_parse_db_ts(text), text)

    def test_naive_stays_naive(self):
        # The tz assumption belongs to the caller, not the parser.
        self.assertIsNone(_parse_db_ts("2026-08-07 10:07:53").tzinfo)

    def test_unparseable_returns_none(self):
        for text in (None, "", "not-a-date", "2026-13-45 99:99:99"):
            self.assertIsNone(_parse_db_ts(text), repr(text))


class RetryAfterSecondsTest(unittest.TestCase):
    def test_respects_header(self):
        self.assertEqual(_retry_after_seconds("7", attempt=0), 7.0)

    def test_missing_header_falls_back_to_backoff(self):
        self.assertEqual(_retry_after_seconds(None, attempt=0), 1.0)
        self.assertEqual(_retry_after_seconds(None, attempt=3), 8.0)

    def test_malformed_header_falls_back_to_backoff(self):
        # A non-numeric Retry-After must not crash the retry loop.
        self.assertEqual(_retry_after_seconds("not-a-number", attempt=2), 4.0)

    def test_non_finite_header_falls_back_to_backoff(self):
        # float() accepts these without raising ValueError — inf/nan would
        # otherwise reach time.sleep and crash (OverflowError/ValueError).
        self.assertEqual(_retry_after_seconds("inf", attempt=1), 2.0)
        self.assertEqual(_retry_after_seconds("-inf", attempt=1), 2.0)
        self.assertEqual(_retry_after_seconds("nan", attempt=1), 2.0)

    def test_negative_header_is_clamped(self):
        self.assertEqual(_retry_after_seconds("-5", attempt=0), 0.0)

    def test_huge_header_is_clamped(self):
        # A legitimate but huge Retry-After (e.g. daily quota reset) shouldn't
        # stall a backfill for its full duration unnoticed.
        self.assertEqual(_retry_after_seconds("3600", attempt=0), 60.0)


class StripAccentsTest(unittest.TestCase):
    def test_removes_diacritics_and_lowercases(self):
        self.assertEqual(_strip_accents("São Paulo"), "sao paulo")
        self.assertEqual(_strip_accents("ARRAIÁ"), "arraia")

    def test_none_passthrough(self):
        self.assertIsNone(_strip_accents(None))

    def test_unaccented_query_matches_accented_text(self):
        # The whole point: a no-accent query normalizes to the same string.
        self.assertEqual(_strip_accents("conciliacao"), _strip_accents("conciliação"))


if __name__ == "__main__":
    unittest.main()
