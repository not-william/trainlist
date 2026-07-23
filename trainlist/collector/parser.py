import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass

GZIP_MAGIC = b"\x1f\x8b"


@dataclass
class Schedule:
    rid: str
    ssd: str
    toc: str
    origin_tpl: str
    origin_dep: str
    dest_tpl: str
    sched_arr: str
    cancelled: bool


@dataclass
class Arrival:
    rid: str
    tpl: str
    at: str


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_message(raw: bytes) -> list[Schedule | Arrival]:
    """Parse one Darwin Pport message (plain or gzipped XML).

    Namespace-agnostic: Darwin nests several schema namespaces inside the
    Pport envelope, so we match on local tag names only.
    """
    if raw[:2] == GZIP_MAGIC:
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    out: list[Schedule | Arrival] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "schedule":
            s = _parse_schedule(el)
            if s is not None:
                out.append(s)
        elif name == "TS":
            out.extend(_parse_ts(el))
    return out


def _parse_schedule(el) -> Schedule | None:
    rid, ssd, toc = el.get("rid"), el.get("ssd"), el.get("toc")
    if not (rid and ssd and toc):
        return None
    origin = dest = None
    for child in el:
        name = _local(child.tag)
        if name in ("OR", "OPOR") and origin is None:
            origin = child
        elif name in ("DT", "OPDT"):
            dest = child
    if origin is None or dest is None:
        return None
    origin_tpl, dest_tpl = origin.get("tpl"), dest.get("tpl")
    origin_dep = origin.get("wtd") or origin.get("ptd")
    sched_arr = dest.get("wta") or dest.get("pta")
    if not (origin_tpl and dest_tpl and origin_dep and sched_arr):
        return None
    return Schedule(
        rid=rid,
        ssd=ssd,
        toc=toc,
        origin_tpl=origin_tpl,
        origin_dep=origin_dep,
        dest_tpl=dest_tpl,
        sched_arr=sched_arr,
        cancelled=dest.get("can") == "true",
    )


def _parse_ts(el) -> list[Arrival]:
    rid = el.get("rid")
    if not rid:
        return []
    out = []
    for loc in el:
        if _local(loc.tag) != "Location":
            continue
        tpl = loc.get("tpl")
        if not tpl:
            continue
        for sub in loc:
            if _local(sub.tag) == "arr":
                at = sub.get("at")
                if at:
                    out.append(Arrival(rid=rid, tpl=tpl, at=at))
    return out
