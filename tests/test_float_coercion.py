"""Unit tests for the ShotGrid Integer→Float auto-coercion helper.

ShotGrid rejects a JSON integer sent to a Float-typed field (e.g. ``Cut.fps``)
with a Fault like ``API create() Cut.fps expected [BigDecimal, Float, NilClass]
... but got Integer``. ``shotgrid._coerce_float_fields`` parses that Fault,
finds the offending field, and coerces the int to float so ``sg_create_impl`` /
``sg_update_impl`` can transparently retry. These tests exercise the pure
helper plus the Fault-matching regex; no live ShotGrid is needed.
"""

from fpt_mcp.shotgrid import _FLOAT_EXPECTED_RE, _coerce_float_fields

# Realistic ShotGrid Fault messages for an int sent to a Float field.
_CREATE_FAULT = (
    "API create() Cut.fps expected [BigDecimal, Float, NilClass] "
    "but got Integer 25"
)
_UPDATE_FAULT = (
    "API update() Shot.sg_some_float expected [Float, NilClass] "
    "but got Integer 3"
)


def test_coerces_int_to_float_on_create_fault():
    data = {"code": "Master v1", "fps": 25}
    fixed = _coerce_float_fields(data, _CREATE_FAULT)
    assert fixed is not None
    assert fixed["fps"] == 25.0
    assert isinstance(fixed["fps"], float)
    # Other fields are left untouched.
    assert fixed["code"] == "Master v1"


def test_does_not_mutate_input():
    data = {"fps": 25}
    _coerce_float_fields(data, _CREATE_FAULT)
    assert isinstance(data["fps"], int)  # original payload left intact


def test_generic_across_entities_and_fields():
    # Not fps, not Cut — any Float field on any entity is covered.
    data = {"sg_some_float": 3}
    fixed = _coerce_float_fields(data, _UPDATE_FAULT)
    assert fixed is not None
    assert fixed["sg_some_float"] == 3.0
    assert isinstance(fixed["sg_some_float"], float)


def test_returns_none_for_unrelated_error():
    assert _coerce_float_fields({"fps": 25}, "Permission denied") is None


def test_returns_none_when_field_absent_from_data():
    # Fault names fps but the payload doesn't carry it → nothing to coerce.
    assert _coerce_float_fields({"code": "X"}, _CREATE_FAULT) is None


def test_returns_none_when_value_already_float():
    # Field present but already a float → not an int → no-op.
    assert _coerce_float_fields({"fps": 25.0}, _CREATE_FAULT) is None


def test_regex_captures_field_name_for_create_and_update():
    m = _FLOAT_EXPECTED_RE.search(_CREATE_FAULT)
    assert m and m.group(1) == "fps"
    m = _FLOAT_EXPECTED_RE.search(_UPDATE_FAULT)
    assert m and m.group(1) == "sg_some_float"
