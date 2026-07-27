import json

from bricks.log import bind_run_id, configure_logging, get_logger, run_context


def emitted(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_output_is_json(capsys):
    configure_logging("INFO")
    get_logger("test").info("run_started", source="dealabs")

    (record,) = emitted(capsys)
    assert record["event"] == "run_started"
    assert record["source"] == "dealabs"
    assert record["level"] == "info"
    assert record["timestamp"].endswith("Z")


def test_run_id_is_injected_into_every_record(capsys):
    configure_logging("INFO")
    bind_run_id(42)
    get_logger("test").info("first")
    get_logger("other").info("second")

    assert [record["run_id"] for record in emitted(capsys)] == [42, 42]


def test_run_context_unbinds_on_exit(capsys):
    configure_logging("INFO")
    with run_context(7):
        get_logger("test").info("inside")
    get_logger("test").info("outside")

    inside, outside = emitted(capsys)
    assert inside["run_id"] == 7
    assert "run_id" not in outside


def test_run_context_unbinds_after_an_exception(capsys):
    configure_logging("INFO")
    try:
        with run_context(7):
            raise RuntimeError("parser exploded")
    except RuntimeError:
        pass
    get_logger("test").info("after")

    (record,) = emitted(capsys)
    assert "run_id" not in record


def test_level_filters_records(capsys):
    configure_logging("WARNING")
    get_logger("test").info("ignored")
    get_logger("test").warning("kept")

    assert [record["event"] for record in emitted(capsys)] == ["kept"]
