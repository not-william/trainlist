from trainlist.collector import matcher, parser


def make_schedule(toc="GR", origin="KNGX", dest="EDINBUR"):
    return parser.Schedule(
        rid="r1",
        ssd="2026-07-23",
        toc=toc,
        origin_tpl=origin,
        origin_dep="09:00",
        dest_tpl=dest,
        sched_arr="13:20",
        cancelled=False,
    )


def test_match(seeded):
    index = matcher.load_route_index(seeded)
    assert matcher.match(index, make_schedule()) == 1


def test_match_reverse_direction(seeded):
    index = matcher.load_route_index(seeded)
    assert matcher.match(index, make_schedule(origin="EDINBUR", dest="KNGX")) == 1


def test_no_match_wrong_toc(seeded):
    index = matcher.load_route_index(seeded)
    assert matcher.match(index, make_schedule(toc="LD")) is None


def test_no_match_wrong_endpoints(seeded):
    index = matcher.load_route_index(seeded)
    assert matcher.match(index, make_schedule(dest="YORK")) is None
