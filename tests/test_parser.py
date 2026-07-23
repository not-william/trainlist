import gzip
from pathlib import Path
from xml.etree import ElementTree

import pytest

from trainlist.collector import parser

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_schedule():
    msgs = parser.parse_message(read("schedule.xml"))
    assert len(msgs) == 1
    s = msgs[0]
    assert isinstance(s, parser.Schedule)
    assert s.rid == "202607238012345"
    assert s.ssd == "2026-07-23"
    assert s.toc == "GR"
    assert s.origin_tpl == "KNGX"
    assert s.origin_dep == "09:00"
    assert s.dest_tpl == "EDINBUR"
    assert s.sched_arr == "13:20"
    assert s.cancelled is False


def test_parse_ts_actual_arrivals_only():
    msgs = parser.parse_message(read("ts.xml"))
    assert msgs == [parser.Arrival(rid="202607238012345", tpl="EDINBUR", at="13:22")]


def test_parse_cancellation():
    (s,) = parser.parse_message(read("cancel.xml"))
    assert s.cancelled is True


def test_parse_gzipped():
    msgs = parser.parse_message(gzip.compress(read("ts.xml")))
    assert len(msgs) == 1


def test_malformed_raises_parse_error():
    with pytest.raises(ElementTree.ParseError):
        parser.parse_message(read("malformed.xml"))


def test_untracked_message_returns_empty():
    xml = b'<Pport xmlns="http://www.thalesgroup.com/rtti/PushPort/v16"><uR><trainAlert/></uR></Pport>'
    assert parser.parse_message(xml) == []
