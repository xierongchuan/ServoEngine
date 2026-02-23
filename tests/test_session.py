"""Tests for src.core.session — trading session awareness."""

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_config():
    """Provide INTRADAY_SETTINGS config for all tests."""
    config = {
        "INTRADAY_SETTINGS": {
            "sessions": {
                "enabled": True,
                "definitions": {
                    "ASIAN": {"start_utc": 0, "end_utc": 8},
                    "EUROPEAN": {"start_utc": 7, "end_utc": 15},
                    "US": {"start_utc": 13, "end_utc": 21},
                },
                "overlap_bonus": 1,
                "dead_zone_hours": [21, 22, 23],
                "dead_zone_penalty": -2,
            }
        }
    }
    with patch("src.core.session.BOT_CONFIG", config):
        yield config


def _session_at_hour(hour):
    """Call get_session_info with a mocked UTC hour."""
    with patch("src.core.session._get_current_utc_hour", return_value=hour):
        from src.core.session import get_session_info
        return get_session_info()


class TestGetSessionInfo:
    """Test get_session_info() at various UTC hours."""

    def test_asian_session_only(self):
        """Hour 3 UTC — only ASIAN session active."""
        result = _session_at_hour(3)

        assert result["active_sessions"] == ["ASIAN"]
        assert result["is_overlap"] is False
        assert result["session_quality"] == "MEDIUM"
        assert result["quality_score_adj"] == 0

    def test_asian_european_overlap(self):
        """Hour 7 UTC — ASIAN + EUROPEAN overlap."""
        result = _session_at_hour(7)

        assert "ASIAN" in result["active_sessions"]
        assert "EUROPEAN" in result["active_sessions"]
        assert result["is_overlap"] is True
        assert result["session_quality"] == "HIGH"
        assert result["quality_score_adj"] == 1

    def test_european_us_overlap(self):
        """Hour 14 UTC — EUROPEAN + US overlap."""
        result = _session_at_hour(14)

        assert "EUROPEAN" in result["active_sessions"]
        assert "US" in result["active_sessions"]
        assert result["is_overlap"] is True
        assert result["session_quality"] == "HIGH"

    def test_us_session_only(self):
        """Hour 16 UTC — only US session."""
        result = _session_at_hour(16)

        assert result["active_sessions"] == ["US"]
        assert result["is_overlap"] is False
        assert result["session_quality"] == "MEDIUM"

    def test_dead_zone(self):
        """Hour 22 UTC — dead zone."""
        result = _session_at_hour(22)

        assert result["is_dead_zone"] is True
        assert result["session_quality"] == "DEAD"
        assert result["quality_score_adj"] == -2

    def test_no_session_not_dead(self):
        """Hour 23 is dead zone, but hour 8 is between ASIAN end and no other session."""
        result = _session_at_hour(8)

        # ASIAN ends at 8 (exclusive), no other active
        # Actually EUROPEAN is 7-15, so 8 is in EUROPEAN only
        assert "EUROPEAN" in result["active_sessions"]
        assert result["session_quality"] == "MEDIUM"

    def test_sessions_disabled(self):
        """Sessions disabled returns default."""
        config = {
            "INTRADAY_SETTINGS": {
                "sessions": {"enabled": False}
            }
        }
        with patch("src.core.session.BOT_CONFIG", config):
            with patch("src.core.session._get_current_utc_hour", return_value=10):
                from src.core.session import get_session_info
                result = get_session_info()

                assert result["session_quality"] == "MEDIUM"
                assert result["quality_score_adj"] == 0
                assert result["active_sessions"] == []
                assert result["is_dead_zone"] is False

    def test_result_has_all_keys(self):
        """Verify all expected keys are present."""
        result = _session_at_hour(10)

        expected_keys = {
            "current_hour_utc", "active_sessions", "is_overlap",
            "is_dead_zone", "session_quality", "quality_score_adj"
        }
        assert expected_keys == set(result.keys())

    def test_low_quality_no_session(self):
        """No active session and not dead zone -> LOW quality."""
        # Configure with narrow sessions to have a gap
        config = {
            "INTRADAY_SETTINGS": {
                "sessions": {
                    "enabled": True,
                    "definitions": {
                        "ASIAN": {"start_utc": 0, "end_utc": 6},
                    },
                    "overlap_bonus": 1,
                    "dead_zone_hours": [22, 23],
                    "dead_zone_penalty": -2,
                }
            }
        }
        with patch("src.core.session.BOT_CONFIG", config):
            with patch("src.core.session._get_current_utc_hour", return_value=10):
                from src.core.session import get_session_info
                result = get_session_info()

                assert result["active_sessions"] == []
                assert result["session_quality"] == "LOW"
                assert result["quality_score_adj"] == -1


class TestHourInRange:
    """Test _hour_in_range helper."""

    def test_normal_range(self):
        from src.core.session import _hour_in_range
        assert _hour_in_range(5, 0, 8) is True
        assert _hour_in_range(8, 0, 8) is False  # end is exclusive
        assert _hour_in_range(10, 0, 8) is False

    def test_wrap_around(self):
        from src.core.session import _hour_in_range
        assert _hour_in_range(23, 22, 6) is True
        assert _hour_in_range(2, 22, 6) is True
        assert _hour_in_range(10, 22, 6) is False

    def test_same_start_end(self):
        from src.core.session import _hour_in_range
        # start == end means empty range
        assert _hour_in_range(5, 5, 5) is False
