import pytest

from cli_layer import build_parser


def _args(*argv):
    return build_parser().parse_args(list(argv))


def test_cb_picks_parser_defaults():
    a = _args("cb-picks")
    assert a.top == 20
    assert a.db == "hs300.db"
    assert a.dry_run is False
    assert a.weights is None


def test_cb_picks_parser_accepts_weights():
    a = _args("cb-picks", "--weights", "a=0.5,b=0.25,c=0.25")
    assert a.weights == "a=0.5,b=0.25,c=0.25"


def test_cb_alerts_parser_accepts_json():
    a = _args("cb-alerts", "--json")
    assert a.json is True


def test_parse_weights_string():
    from cli_layer import _parse_weights
    w = _parse_weights("a=0.5,b=0.25,c=0.25")
    assert w == {"a": 0.5, "b": 0.25, "c": 0.25}


def test_parse_weights_none_returns_none():
    from cli_layer import _parse_weights
    assert _parse_weights(None) is None
    assert _parse_weights("") is None


def test_parse_weights_invalid_raises():
    from cli_layer import _parse_weights
    with pytest.raises(ValueError):
        _parse_weights("a=0.5,b=oops")


def test_parse_weights_unknown_group_raises():
    from cli_layer import _parse_weights
    with pytest.raises(ValueError):
        _parse_weights("x=0.5")


def test_parse_weights_keeps_raw_values():
    # CLI 层 _parse_weights 只解析不归一化；归一化在 cb_pipeline 层做
    from cli_layer import _parse_weights
    w = _parse_weights("a=2,b=1")
    assert w == {"a": 2.0, "b": 1.0}
