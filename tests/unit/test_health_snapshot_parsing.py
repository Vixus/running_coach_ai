"""Unit tests for parse_health_snapshot — covers all Garmin API schema variants
and the morning check-in data path end-to-end.

These tests pin the exact field names and fallback paths so that Garmin firmware
changes (e.g., renamed score fields) are caught immediately rather than silently
returning None to the athlete.

Key invariants protected here:
  - sleep score: dailySleepDTO.sleepScores.totalScore (modern) + legacy fallbacks
  - HRV: hrvSummary.lastNightAvg with lastNight fallback
  - training readiness: list *or* dict response (Garmin API returns a list)
  - upsert: None values never overwrite existing data on a second fetch
  - morning check-in: Claude's prompt contains the actual parsed values
"""

from datetime import date
from unittest.mock import MagicMock, call, patch


TODAY = date(2026, 4, 18)
ATHLETE_ID = 42
_PARSER = "running_coach_ai.garmin.parser"
_ADAPTER = "running_coach_ai.coach.adapter"


# ---------------------------------------------------------------------------
# Helpers — raw Garmin API response builders
# ---------------------------------------------------------------------------

def _sleep_raw(*, total_score=82, sleep_time_seconds=26760, schema="modern"):
    """Build a raw sleep API response for a given schema variant."""
    if schema == "modern":
        # Firmware 2024+: dailySleepDTO.sleepScores.totalScore
        dto = {
            "sleepTimeSeconds": sleep_time_seconds,
            "sleepScores": {"totalScore": total_score},
        }
    elif schema == "legacy_overall_value":
        # Older firmware: dailySleepDTO.sleepScores.overall.value
        dto = {
            "sleepTimeSeconds": sleep_time_seconds,
            "sleepScores": {"overall": {"value": total_score}},
        }
    elif schema == "legacy_overall_flat":
        # Flat overallSleepScore key on the DTO
        dto = {"sleepTimeSeconds": sleep_time_seconds, "overallSleepScore": total_score}
    elif schema == "legacy_dto_sleepScore":
        dto = {"sleepTimeSeconds": sleep_time_seconds, "sleepScore": total_score}
    elif schema == "legacy_root_sleepScore":
        # sleepScore at the root of raw["sleep"], not inside dailySleepDTO
        return {
            "dailySleepDTO": {"sleepTimeSeconds": sleep_time_seconds},
            "sleepScore": total_score,
        }
    else:
        dto = {"sleepTimeSeconds": sleep_time_seconds}  # no score key at all
    return {"dailySleepDTO": dto}


def _hrv_raw(*, value=32, status="BALANCED", field="lastNightAvg"):
    summary = {"status": status, field: value}
    return {"hrvSummary": summary}


def _rhr_raw(*, value=52):
    return {
        "allMetrics": {
            "metricsMap": {"WELLNESS_RESTING_HEART_RATE": [{"value": value}]}
        }
    }


def _bb_raw(values):
    """values: list of [timestamp_ms, level] pairs."""
    return [{"bodyBatteryValuesArray": values}]


def _empty_raw():
    return {k: None for k in ("sleep", "hrv", "rhr", "body_battery",
                               "stress", "steps", "spo2", "training_readiness")}


def _db_no_existing():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def _db_with_existing(snapshot):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = snapshot
    return db


def _parse(raw, db=None):
    from running_coach_ai.garmin.parser import parse_health_snapshot
    return parse_health_snapshot(raw, ATHLETE_ID, TODAY, db or _db_no_existing())


def _new_snapshot(db):
    """Return the HealthSnapshot instance passed to db.add()."""
    assert db.add.call_count == 1, "Expected exactly one db.add() call"
    return db.add.call_args[0][0]


# ---------------------------------------------------------------------------
# Sleep score — schema variants
# ---------------------------------------------------------------------------

class TestSleepScoreParsing:

    def test_modern_firmware_total_score(self):
        """dailySleepDTO.sleepScores.totalScore — the field that was missing before this fix."""
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": _sleep_raw(total_score=82, schema="modern")}, db)
        assert _new_snapshot(db).sleep_score == 82

    def test_legacy_overall_value(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": _sleep_raw(total_score=75, schema="legacy_overall_value")}, db)
        assert _new_snapshot(db).sleep_score == 75

    def test_legacy_overall_flat(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": _sleep_raw(total_score=68, schema="legacy_overall_flat")}, db)
        assert _new_snapshot(db).sleep_score == 68

    def test_legacy_dto_sleep_score(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": _sleep_raw(total_score=90, schema="legacy_dto_sleepScore")}, db)
        assert _new_snapshot(db).sleep_score == 90

    def test_legacy_root_sleep_score(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": _sleep_raw(total_score=77, schema="legacy_root_sleepScore")}, db)
        assert _new_snapshot(db).sleep_score == 77

    def test_no_score_field_returns_none(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": {"dailySleepDTO": {"sleepTimeSeconds": 26760}}}, db)
        assert _new_snapshot(db).sleep_score is None

    def test_no_sleep_data_returns_none(self):
        db = _db_no_existing()
        _parse(_empty_raw(), db)
        assert _new_snapshot(db).sleep_score is None


# ---------------------------------------------------------------------------
# Sleep duration
# ---------------------------------------------------------------------------

class TestSleepDuration:

    def test_7h26m_parsed_from_seconds(self):
        """7h 26m = 26760 s — the value must survive the parse intact."""
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": _sleep_raw(sleep_time_seconds=26760)}, db)
        assert _new_snapshot(db).sleep_duration_seconds == 26760

    def test_absent_sleep_time_is_none(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": {"dailySleepDTO": {"sleepScores": {"totalScore": 80}}}}, db)
        assert _new_snapshot(db).sleep_duration_seconds is None

    def test_zero_sleep_time_stored_as_zero(self):
        # The parser stores whatever _safe_get returns — 0 is passed through unchanged.
        # Garmin never actually sends sleepTimeSeconds=0 for a real recording, but the
        # parser doesn't special-case it.
        db = _db_no_existing()
        _parse({**_empty_raw(), "sleep": _sleep_raw(sleep_time_seconds=0)}, db)
        assert _new_snapshot(db).sleep_duration_seconds == 0


# ---------------------------------------------------------------------------
# HRV
# ---------------------------------------------------------------------------

class TestHRVParsing:

    def test_last_night_avg(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "hrv": _hrv_raw(value=32, field="lastNightAvg")}, db)
        assert _new_snapshot(db).hrv_score == 32

    def test_fallback_to_last_night_when_avg_absent(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "hrv": _hrv_raw(value=29, field="lastNight")}, db)
        assert _new_snapshot(db).hrv_score == 29

    def test_hrv_status_balanced(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "hrv": _hrv_raw(value=32, status="BALANCED")}, db)
        assert _new_snapshot(db).hrv_status == "BALANCED"

    def test_hrv_status_unbalanced(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "hrv": _hrv_raw(value=20, status="UNBALANCED")}, db)
        assert _new_snapshot(db).hrv_status == "UNBALANCED"

    def test_missing_hrv_summary_returns_none(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "hrv": {"noSummaryHere": {}}}, db)
        assert _new_snapshot(db).hrv_score is None

    def test_no_hrv_data_returns_none(self):
        db = _db_no_existing()
        _parse(_empty_raw(), db)
        assert _new_snapshot(db).hrv_score is None


# ---------------------------------------------------------------------------
# Resting HR
# ---------------------------------------------------------------------------

class TestRHRParsing:

    def test_wellness_resting_heart_rate_path(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "rhr": _rhr_raw(value=52)}, db)
        assert _new_snapshot(db).resting_hr == 52

    def test_fallback_resting_heart_rate_key(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "rhr": {"restingHeartRate": 55}}, db)
        assert _new_snapshot(db).resting_hr == 55

    def test_no_rhr_data_returns_none(self):
        db = _db_no_existing()
        _parse(_empty_raw(), db)
        assert _new_snapshot(db).resting_hr is None


# ---------------------------------------------------------------------------
# Body battery
# ---------------------------------------------------------------------------

class TestBodyBatteryParsing:

    def test_peak_is_start_last_is_end(self):
        """Peak across the day = start (overnight recovery); last = current charge."""
        vals = [[1000, 40], [2000, 80], [3000, 89], [4000, 72], [5000, 65]]
        db = _db_no_existing()
        _parse({**_empty_raw(), "body_battery": _bb_raw(vals)}, db)
        snap = _new_snapshot(db)
        assert snap.body_battery_start == 89
        assert snap.body_battery_end == 65

    def test_single_value_used_for_both(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "body_battery": _bb_raw([[1000, 77]])}, db)
        snap = _new_snapshot(db)
        assert snap.body_battery_start == 77
        assert snap.body_battery_end == 77

    def test_none_entries_in_array_are_skipped(self):
        vals = [[1000, None], [2000, 85], [3000, 70]]
        db = _db_no_existing()
        _parse({**_empty_raw(), "body_battery": _bb_raw(vals)}, db)
        snap = _new_snapshot(db)
        assert snap.body_battery_start == 85
        assert snap.body_battery_end == 70

    def test_empty_array_returns_none(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "body_battery": [{"bodyBatteryValuesArray": []}]}, db)
        assert _new_snapshot(db).body_battery_start is None

    def test_no_body_battery_data_returns_none(self):
        db = _db_no_existing()
        _parse(_empty_raw(), db)
        assert _new_snapshot(db).body_battery_start is None


# ---------------------------------------------------------------------------
# Training readiness — dict and list responses
# ---------------------------------------------------------------------------

class TestTrainingReadinessParsing:

    def test_list_response_is_unwrapped(self):
        """Garmin returns a list; the parser must extract the first element's score."""
        db = _db_no_existing()
        _parse({**_empty_raw(), "training_readiness": [{"score": 65, "calendarDate": "2026-04-18"}]}, db)
        assert _new_snapshot(db).training_readiness == 65

    def test_list_with_trainingReadinessScore_key(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "training_readiness": [{"trainingReadinessScore": 72}]}, db)
        assert _new_snapshot(db).training_readiness == 72

    def test_dict_response_score_key(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "training_readiness": {"score": 68}}, db)
        assert _new_snapshot(db).training_readiness == 68

    def test_dict_response_trainingReadinessScore_key(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "training_readiness": {"trainingReadinessScore": 74}}, db)
        assert _new_snapshot(db).training_readiness == 74

    def test_result_is_cast_to_int(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "training_readiness": [{"score": 68.9}]}, db)
        val = _new_snapshot(db).training_readiness
        assert val == 68
        assert isinstance(val, int)

    def test_no_training_readiness_returns_none(self):
        db = _db_no_existing()
        _parse(_empty_raw(), db)
        assert _new_snapshot(db).training_readiness is None

    def test_empty_list_returns_none(self):
        db = _db_no_existing()
        _parse({**_empty_raw(), "training_readiness": []}, db)
        assert _new_snapshot(db).training_readiness is None


# ---------------------------------------------------------------------------
# Upsert behavior — preserves best-known data across multiple fetches
# ---------------------------------------------------------------------------

class TestUpsertBehavior:

    def test_new_snapshot_calls_db_add(self):
        db = _db_no_existing()
        _parse({**_empty_raw(),
                "sleep": _sleep_raw(total_score=82, sleep_time_seconds=26760),
                "hrv": _hrv_raw(value=32)}, db)
        db.add.assert_called_once()
        db.commit.assert_called()

    def test_existing_snapshot_updated_not_duplicated(self):
        """On upsert, setattr is used — db.add must NOT be called again."""
        existing = MagicMock()
        existing.sleep_score = None
        existing.hrv_score = None
        db = _db_with_existing(existing)
        _parse({**_empty_raw(),
                "sleep": _sleep_raw(total_score=82),
                "hrv": _hrv_raw(value=32)}, db)
        db.add.assert_not_called()
        assert existing.sleep_score == 82
        assert existing.hrv_score == 32

    def test_none_values_do_not_overwrite_existing_data(self):
        """If a second fetch has no sleep data, the first fetch's value is kept."""
        existing = MagicMock()
        existing.sleep_score = 75  # populated on first fetch
        existing.hrv_score = 40
        db = _db_with_existing(existing)
        # Second fetch: sleep unavailable, HRV updated
        _parse({**_empty_raw(), "hrv": _hrv_raw(value=35)}, db)
        assert existing.sleep_score == 75, "sleep_score must not be overwritten by None"
        assert existing.hrv_score == 35

    def test_all_fields_populated_on_new_record(self):
        """A full fetch writes all fields to the new snapshot."""
        db = _db_no_existing()
        _parse({
            "sleep": _sleep_raw(total_score=82, sleep_time_seconds=26760),
            "hrv": _hrv_raw(value=32, status="BALANCED"),
            "rhr": _rhr_raw(value=52),
            "body_battery": _bb_raw([[0, 85], [1000, 90], [2000, 70]]),
            "stress": {"avgStressLevel": 21},
            "steps": [{"steps": 3000}, {"steps": 2000}],
            "spo2": {"averageSpO2": 96.5},
            "training_readiness": [{"score": 68}],
        }, db)
        snap = _new_snapshot(db)
        assert snap.sleep_score == 82
        assert snap.sleep_duration_seconds == 26760
        assert snap.hrv_score == 32
        assert snap.hrv_status == "BALANCED"
        assert snap.resting_hr == 52
        assert snap.body_battery_start == 90
        assert snap.body_battery_end == 70
        assert snap.stress_avg == 21
        assert snap.steps == 5000
        assert snap.spo2_avg == 96.5
        assert snap.training_readiness == 68


# ---------------------------------------------------------------------------
# Morning check-in — Claude prompt contains the actual parsed values
# ---------------------------------------------------------------------------

class TestMorningCheckinMessageContent:
    """End-to-end: parsed health values must appear in the prompt sent to Claude."""

    @patch(f"{_ADAPTER}.extract_and_apply_plan", return_value="msg")
    @patch(f"{_ADAPTER}.call_claude", return_value="msg")
    @patch(f"{_ADAPTER}.scoped_query")
    @patch("running_coach_ai.coach.notify.notify")
    @patch(f"{_PARSER}.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    @patch("running_coach_ai.weather.client.summarise_forecast", return_value=[])
    @patch("running_coach_ai.weather.client.get_forecast", return_value={})
    def test_prompt_contains_sleep_score_hrv_and_rhr(
        self,
        _forecast, _summarise, _garmin_client, _health_raw,
        mock_parse, _send_dm, mock_scoped, mock_claude, _extract,
    ):
        from running_coach_ai.coach.adapter import run_morning_checkin

        snap = MagicMock()
        snap.date = TODAY
        snap.sleep_score = 82
        snap.hrv_score = 32
        snap.hrv_status = "BALANCED"
        snap.body_battery_start = 89
        snap.resting_hr = 52
        snap.stress_avg = 21
        snap.training_readiness = 68
        mock_parse.return_value = snap

        mock_scoped.return_value.filter.return_value.first.return_value = None

        athlete = MagicMock()
        athlete.id = 1
        athlete.name = "Simon"
        athlete.timezone = "America/New_York"
        athlete.garmin_email = "simon@example.com"
        athlete.garmin_password_encrypted = b"enc"
        athlete.home_lat = 40.7
        athlete.home_lon = -74.0
        athlete.last_morning_checkin_date = None
        athlete.coach_key = "default"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        # Pin time to a daytime hour so the 06:00 local floor doesn't return early
        from datetime import datetime as _dt, timezone as _tz
        fake_local = _dt(TODAY.year, TODAY.month, TODAY.day, 8, 0, tzinfo=_tz.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: _dt(*args, **kwargs)
            run_morning_checkin(athlete, db)

        assert mock_claude.called, "Claude must be called during morning check-in"
        prompt = mock_claude.call_args[0][1][0]["content"]

        assert "82" in prompt, f"Sleep score 82 missing from prompt:\n{prompt}"
        assert "32" in prompt, f"HRV 32 missing from prompt:\n{prompt}"
        assert "52" in prompt, f"Resting HR 52 missing from prompt:\n{prompt}"
        assert "68" in prompt, f"Training readiness 68 missing from prompt:\n{prompt}"

    @patch(f"{_ADAPTER}.extract_and_apply_plan", return_value="msg")
    @patch(f"{_ADAPTER}.call_claude", return_value="msg")
    @patch(f"{_ADAPTER}.scoped_query")
    @patch("running_coach_ai.coach.notify.notify")
    @patch(f"{_PARSER}.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    @patch("running_coach_ai.weather.client.summarise_forecast", return_value=[])
    @patch("running_coach_ai.weather.client.get_forecast", return_value={})
    def test_none_sleep_score_shows_none_in_prompt(
        self,
        _forecast, _summarise, _garmin_client, _health_raw,
        mock_parse, _send_dm, mock_scoped, mock_claude, _extract,
    ):
        """If parse returns sleep_score=None, the prompt must say 'None' — not a stale value."""
        from running_coach_ai.coach.adapter import run_morning_checkin

        snap = MagicMock()
        snap.date = TODAY
        snap.sleep_score = None  # the field under test
        snap.hrv_score = 32
        snap.hrv_status = "BALANCED"
        snap.body_battery_start = 89
        snap.resting_hr = 52
        snap.stress_avg = 21
        # training_readiness is set so the morning-data gate passes — we're
        # specifically testing that a missing sleep_score still surfaces as
        # "None" in the prompt, not a stale value.
        snap.training_readiness = 68
        mock_parse.return_value = snap

        mock_scoped.return_value.filter.return_value.first.return_value = None

        athlete = MagicMock()
        athlete.id = 1
        athlete.name = "Simon"
        athlete.timezone = "America/New_York"
        athlete.garmin_email = "simon@example.com"
        athlete.garmin_password_encrypted = b"enc"
        athlete.home_lat = 40.7
        athlete.home_lon = -74.0
        athlete.last_morning_checkin_date = None
        athlete.coach_key = "default"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        # Past noon so the morning-data gate is bypassed (production proceeds
        # with whatever partial snapshot we have).
        from datetime import datetime as _dt, timezone as _tz
        fake_local = _dt(TODAY.year, TODAY.month, TODAY.day, 13, 0, tzinfo=_tz.utc)
        with patch(f"{_ADAPTER}.datetime") as mock_dt:
            mock_dt.now.return_value = fake_local
            mock_dt.side_effect = lambda *args, **kwargs: _dt(*args, **kwargs)
            run_morning_checkin(athlete, db)

        assert mock_claude.called, "Past noon, partial snapshot — Claude must still be called"
        prompt = mock_claude.call_args[0][1][0]["content"]
        assert "Sleep score: None" in prompt, (
            "Prompt must show 'None' for missing sleep score so Claude doesn't hallucinate a value"
        )


# ---------------------------------------------------------------------------
# Conversation live re-fetch — stale snapshot with all-None key fields
# ---------------------------------------------------------------------------

_CONV = "running_coach_ai.coach.conversation"


def _make_stale_snapshot():
    """A snapshot row that exists in the DB but has no usable health data."""
    s = MagicMock()
    s.date = TODAY
    s.sleep_score = None
    s.hrv_score = None
    s.body_battery_start = None
    s.hrv_status = None
    s.resting_hr = None
    s.stress_avg = None
    s.sleep_duration_seconds = None
    s.body_battery_end = None
    s.steps = None
    s.spo2_avg = None
    s.training_readiness = None
    return s


def _make_good_snapshot():
    s = MagicMock()
    s.date = TODAY
    s.sleep_score = 82
    s.hrv_score = 32
    s.hrv_status = "BALANCED"
    s.body_battery_start = 89
    s.resting_hr = 52
    s.stress_avg = 21
    s.sleep_duration_seconds = 26760
    s.body_battery_end = 65
    s.steps = 500
    s.spo2_avg = 97.0
    s.training_readiness = 68
    return s


class TestConversationLiveRefetch:
    """build_system_prompt must re-fetch from Garmin when the stored snapshot has
    no usable health data (all key fields None) — not just when no row exists."""

    def _scoped_side_effect(self, stored_snapshot):
        """Side-effect for scoped_query: returns the health snapshot for HealthSnapshot
        queries, and safe empty results for every other model."""
        from running_coach_ai.database.models import HealthSnapshot as _HS

        def _side_effect(db, model, athlete_id):
            r = MagicMock()
            # Cover both .first() and .filter().first() call patterns
            r.first.return_value = None
            r.filter.return_value.first.return_value = None
            r.filter.return_value.all.return_value = []
            r.filter.return_value.order_by.return_value.all.return_value = []
            r.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
            r.filter.return_value.outerjoin.return_value.filter.return_value.first.return_value = None
            if model is _HS:
                r.filter.return_value.first.return_value = stored_snapshot
                r.filter.return_value.order_by.return_value.all.return_value = (
                    [stored_snapshot] if stored_snapshot else []
                )
            return r

        return _side_effect

    def _make_athlete(self):
        a = MagicMock()
        a.id = 1
        a.name = "Simon"
        a.timezone = "America/New_York"
        a.garmin_email = "simon@example.com"
        a.garmin_password_encrypted = b"enc"
        a.home_lat = None
        a.home_lon = None
        a.coach_key = "default"
        return a

    @patch("running_coach_ai.coach.conversation.scoped_query")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_live_fetch_triggered_when_snapshot_has_all_none_key_fields(
        self, mock_garmin, mock_raw, mock_parse, mock_scoped
    ):
        """All three key fields None → live fetch fires."""
        from running_coach_ai.coach.conversation import build_system_prompt
        mock_parse.return_value = _make_good_snapshot()
        mock_scoped.side_effect = self._scoped_side_effect(_make_stale_snapshot())
        build_system_prompt(self._make_athlete(), MagicMock())
        mock_garmin.assert_called_once()
        mock_parse.assert_called_once()

    @patch("running_coach_ai.coach.conversation.scoped_query")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_live_fetch_triggered_when_only_sleep_score_is_none(
        self, mock_garmin, mock_raw, mock_parse, mock_scoped
    ):
        """HRV + body battery stored but sleep_score=None → live fetch still fires.
        This is the exact scenario where the morning check-in stored partial data
        (e.g. broken sleep score parser) and the user asks for their morning report."""
        from running_coach_ai.coach.conversation import build_system_prompt
        partial = _make_stale_snapshot()
        partial.hrv_score = 32          # HRV was stored correctly
        partial.body_battery_start = 89  # body battery was stored correctly
        # sleep_score stays None — the broken field
        mock_parse.return_value = _make_good_snapshot()
        mock_scoped.side_effect = self._scoped_side_effect(partial)
        build_system_prompt(self._make_athlete(), MagicMock())
        mock_garmin.assert_called_once()

    @patch("running_coach_ai.coach.conversation.scoped_query")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_live_fetch_triggered_when_no_snapshot_exists(
        self, mock_garmin, mock_raw, mock_parse, mock_scoped
    ):
        """Original behaviour: no row at all → live fetch fires."""
        from running_coach_ai.coach.conversation import build_system_prompt
        mock_parse.return_value = _make_good_snapshot()
        mock_scoped.side_effect = self._scoped_side_effect(None)
        build_system_prompt(self._make_athlete(), MagicMock())
        mock_garmin.assert_called_once()

    @patch("running_coach_ai.coach.conversation.scoped_query")
    @patch("running_coach_ai.garmin.parser.parse_health_snapshot")
    @patch("running_coach_ai.garmin.client.get_health_snapshot", return_value={})
    @patch("running_coach_ai.garmin.client.get_garmin_client")
    def test_live_fetch_skipped_when_snapshot_has_good_data(
        self, mock_garmin, mock_raw, mock_parse, mock_scoped
    ):
        """If the stored snapshot already has all three key fields populated,
        no live fetch should occur — avoids unnecessary Garmin API calls."""
        from running_coach_ai.coach.conversation import build_system_prompt
        mock_scoped.side_effect = self._scoped_side_effect(_make_good_snapshot())
        build_system_prompt(self._make_athlete(), MagicMock())
        mock_garmin.assert_not_called()
