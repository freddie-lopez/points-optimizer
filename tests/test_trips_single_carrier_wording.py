"""
Re-test 2, R2-6: a line about a lookup that was never made does not speak of
"this lookup".

With a one-carrier row, UNKNOWN (a lookup happened and added nothing) says
"This lookup established nothing further"; NOT LOOKED UP and NOT RECORDED say
"No lookup was made on this run". Both keep the one-carrier domain.
"""
import pytest

from src.models import METAL_REASONS, MetalLookup, MetalStatus

DOMAIN = "the award's own carrier list names one carrier, so the possible carriers are VS"


def _render(status, code):
    return MetalLookup(
        status=status, reason_code=code, detail="x", possible_carriers=("VS",),
        row_carriers=("VS",), on_replay=(status is MetalStatus.NOT_RECORDED),
    ).render()


NEVER_LOOKED_UP = sorted(
    [(MetalStatus.NOT_LOOKED_UP, c) for c in METAL_REASONS[MetalStatus.NOT_LOOKED_UP]]
    + [(MetalStatus.NOT_RECORDED, c) for c in METAL_REASONS[MetalStatus.NOT_RECORDED]],
    key=lambda sc: (sc[0].value, sc[1]),
)


@pytest.mark.parametrize("status,code", NEVER_LOOKED_UP, ids=lambda v: getattr(v, "value", v))
def test_no_lookup_is_described_as_this_lookup(status, code):
    text = _render(status, code)
    assert "This lookup established" not in text
    assert "No lookup was made on this run" in text
    assert DOMAIN in text


@pytest.mark.parametrize("code", ["NO_MATCH", "EMPTY_DATA", "HTTP_404", "TIMEOUT"])
def test_an_unknown_lookup_still_says_it_established_nothing_further(code):
    text = _render(MetalStatus.UNKNOWN, code)
    assert "This lookup established nothing further" in text
    assert DOMAIN in text
