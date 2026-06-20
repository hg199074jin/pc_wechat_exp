"""Tests for engine.services.message._build_where — WHERE clause builder."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.message import _build_where, _date_to_ts, MSG_TYPE_LABELS


class TestBuildWhereNoFilters:
    """_build_where with no filters produces only the base timestamp guard."""

    def test_returns_string_and_list(self):
        clause, params = _build_where(None, None, None, None, None)
        assert isinstance(clause, str)
        assert isinstance(params, list)

    def test_base_clause_present(self):
        clause, _ = _build_where(None, None, None, None, None)
        assert 'create_time > 1000000000' in clause

    def test_no_params(self):
        _, params = _build_where(None, None, None, None, None)
        assert params == []

    def test_only_one_clause(self):
        clause, _ = _build_where(None, None, None, None, None)
        # Should have exactly one clause (no extra ANDs)
        assert clause.count('AND') == 0


class TestBuildWhereDateRange:
    """_build_where with start_date and end_date."""

    def test_both_dates(self):
        clause, params = _build_where('2024-01-01', '2024-12-31', None, None, None)
        assert 'create_time >= ?' in clause
        assert 'create_time <= ?' in clause
        assert len(params) == 2

    def test_date_params_are_timestamps(self):
        _, params = _build_where('2024-06-15', '2024-06-20', None, None, None)
        assert all(isinstance(p, int) for p in params)
        assert params[0] < params[1]

    def test_swapped_dates_auto_corrected(self):
        """When start > end, they are automatically swapped."""
        _, params = _build_where('2024-12-31', '2024-01-01', None, None, None)
        assert params[0] < params[1]

    def test_start_date_only(self):
        clause, params = _build_where('2024-06-01', None, None, None, None)
        assert 'create_time >= ?' in clause
        assert 'create_time <=' not in clause
        assert len(params) == 1

    def test_end_date_only(self):
        clause, params = _build_where(None, '2024-12-31', None, None, None)
        assert 'create_time <=' in clause
        assert 'create_time >=' not in clause
        assert len(params) == 1

    def test_end_of_day_for_end_date(self):
        """End date should use end-of-day (23:59:59) for the timestamp."""
        _, params = _build_where(None, '2024-06-15', None, None, None)
        end_ts = _date_to_ts('2024-06-15', end_of_day=True)
        assert params[0] == end_ts


class TestBuildWhereMsgTypes:
    """_build_where with msg_types filter."""

    def test_single_valid_type(self):
        # Type 3 = [图片] — a valid type in MSG_TYPE_LABELS
        clause, params = _build_where(None, None, '3', None, None)
        assert 'IN (' in clause
        assert 3 in params

    def test_multiple_valid_types(self):
        clause, params = _build_where(None, None, '3,34,43', None, None)
        assert 'IN (' in clause
        assert len(params) == 3
        assert set(params) == {3, 34, 43}

    def test_type_mask_applied(self):
        """Uses local_type & 65535 (0xFFFF) mask."""
        clause, _ = _build_where(None, None, '3', None, None)
        assert '65535' in clause or '0xFFFF' in clause

    def test_type_not_in_labels_filtered_out(self):
        """Types not in MSG_TYPE_LABELS are filtered out."""
        # 99999 is not a valid WeChat message type
        clause, params = _build_where(None, None, '99999', None, None)
        assert 'IN (' not in clause
        assert len(params) == 0

    def test_all_known_types_accepted(self):
        """All types in MSG_TYPE_LABELS should be accepted."""
        type_str = ','.join(str(t) for t in MSG_TYPE_LABELS)
        clause, params = _build_where(None, None, type_str, None, None)
        assert 'IN (' in clause
        assert len(params) == len(MSG_TYPE_LABELS)


class TestBuildWhereKeyword:
    """_build_where with keyword search."""

    def test_keyword_adds_like_clause(self):
        clause, params = _build_where(None, None, None, None, 'hello')
        assert 'message_content LIKE ?' in clause
        assert params[0] == '%hello%'

    def test_keyword_wildcards_wrapped(self):
        _, params = _build_where(None, None, None, None, 'test')
        assert params[0].startswith('%')
        assert params[0].endswith('%')

    def test_keyword_special_chars_escaped(self):
        """LIKE wildcards in keyword must be escaped."""
        _, params = _build_where(None, None, None, None, '50%_off')
        assert '\\%' in params[0]
        assert '\\_' in params[0]

    def test_keyword_chinese(self):
        _, params = _build_where(None, None, None, None, '你好世界')
        assert '你好世界' in params[0]


class TestBuildWhereSender:
    """_build_where with sender filter."""

    def test_sender_self(self):
        clause, params = _build_where(None, None, None, '__self__', None)
        assert 'origin_source = 1' in clause
        assert params == []

    def test_sender_sys(self):
        clause, params = _build_where(None, None, None, '__sys__', None)
        assert '10000' in clause
        assert '10002' in clause

    def test_sender_named_non_group(self):
        """Named sender in non-group chat → origin_source != 1."""
        clause, params = _build_where(None, None, None, 'someone', None, is_group=False)
        assert 'origin_source != 1' in clause

    def test_sender_named_group(self):
        """Named sender in group chat → message_content LIKE prefix."""
        clause, params = _build_where(None, None, None, 'someone', None, is_group=True)
        assert 'message_content LIKE ?' in clause
        assert any('someone' in str(p) for p in params)


class TestBuildWhereInvalidDate:
    """_build_where handles invalid date strings gracefully."""

    def test_invalid_start_date(self):
        """Invalid start_date is silently ignored (no crash)."""
        clause, params = _build_where('not-a-date', None, None, None, None)
        assert isinstance(clause, str)
        assert isinstance(params, list)

    def test_invalid_end_date(self):
        """Invalid end_date is silently ignored."""
        clause, params = _build_where(None, 'xyz', None, None, None)
        assert isinstance(clause, str)
        assert isinstance(params, list)

    def test_both_dates_invalid(self):
        clause, params = _build_where('abc', 'def', None, None, None)
        assert isinstance(clause, str)
        assert isinstance(params, list)

    def test_invalid_date_with_valid_date(self):
        """Invalid start + valid end should still produce the end date clause."""
        clause, params = _build_where('abc', '2024-12-31', None, None, None)
        assert 'create_time <=' in clause

    def test_empty_string_dates(self):
        clause, params = _build_where('', '', None, None, None)
        assert isinstance(clause, str)
        assert isinstance(params, list)


class TestBuildWhereMixedTypes:
    """_build_where with mixed valid/invalid type strings."""

    def test_mixed_valid_and_invalid_drops_all(self):
        """'1,abc,3' raises ValueError on 'abc', which catches and drops ALL types.

        This is known behavior: int('abc') ValueError causes the entire
        list to be reset to [].
        """
        clause, params = _build_where(None, None, '1,abc,3', None, None)
        assert 'IN (' not in clause
        assert len(params) == 0

    def test_all_invalid_types(self):
        clause, params = _build_where(None, None, 'abc,def', None, None)
        assert 'IN (' not in clause
        assert len(params) == 0

    def test_whitespace_handling(self):
        """Whitespace around type numbers is stripped."""
        clause, params = _build_where(None, None, ' 3 , 34 ', None, None)
        assert 'IN (' in clause
        assert len(params) == 2

    def test_trailing_comma(self):
        """Trailing comma produces empty string element, which is filtered."""
        clause, params = _build_where(None, None, '3,', None, None)
        assert 'IN (' in clause
        assert len(params) == 1


class TestBuildWhereCombined:
    """_build_where with multiple filters combined."""

    def test_date_and_keyword(self):
        clause, params = _build_where('2024-01-01', '2024-12-31', None, None, 'test')
        assert 'create_time >=' in clause
        assert 'create_time <=' in clause
        assert 'message_content LIKE ?' in clause
        assert any('test' in str(p) for p in params)

    def test_type_and_sender(self):
        clause, params = _build_where(None, None, '3', '__self__', None)
        assert 'IN (' in clause
        assert 'origin_source = 1' in clause

    def test_all_filters(self):
        clause, params = _build_where(
            '2024-01-01', '2024-12-31', '3,43', '__self__', 'hello'
        )
        assert 'create_time >=' in clause
        assert 'create_time <=' in clause
        assert 'IN (' in clause
        assert 'origin_source = 1' in clause
        assert 'message_content LIKE ?' in clause
