import logging

from app.services.crm.inbox.context import get_inbox_logger, get_request_id, set_request_id


def test_inbox_logger_injects_request_id():
    set_request_id("abc12345")
    logger = get_inbox_logger("test")
    logger.logger.makeRecord(
        "test",
        logging.INFO,
        "",
        0,
        "message",
        args=(),
        exc_info=None,
    )
    _msg, kwargs = logger.process("message", {"extra": {}})
    assert kwargs["extra"]["request_id"] == "abc12345"
    assert get_request_id() == "abc12345"
