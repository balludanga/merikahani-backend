"""The arithmetic of money, in whole paise, so the same rule is written once and tested once."""

from typing import Tuple


def tip_split(gross_paise: int, site_fee_bps: int) -> Tuple[int, int]:
    """(the writer's share, the site's share) of a tip. The site's share rounds half up and the two add up
    to the tip exactly, so not a paisa appears or vanishes."""
    site = (gross_paise * site_fee_bps + 5000) // 10000
    return gross_paise - site, site


def split_gst(gross_paise: int, gst_percent: int) -> Tuple[int, int]:
    """(price without tax, tax) of a price that already includes ``gst_percent`` GST."""
    if gst_percent <= 0:
        return gross_paise, 0
    divisor = 100 + gst_percent
    net = (gross_paise * 100 + divisor // 2) // divisor
    return net, gross_paise - net


def proportional(part_of: int, total: int, whole: int) -> int:
    """``part_of`` out of ``total``, applied to ``whole``, rounded half up (0 when there is no total)."""
    if total <= 0:
        return 0
    return (whole * part_of + total // 2) // total
