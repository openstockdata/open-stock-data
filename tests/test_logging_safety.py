import logging

from open_stock_data.logging_safety import redact_sensitive_text, sanitize_for_logging


def test_redact_sensitive_text_masks_query_params_and_headers():
    raw = (
        "GET https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
        "&apikey=07V86L0YD9URFVSC&symbol=MSFT "
        "Authorization: Bearer super-secret-token"
    )

    masked = redact_sensitive_text(raw)

    assert "07V86L0YD9URFVSC" not in masked
    assert "super-secret-token" not in masked
    assert "apikey=<redacted>" in masked
    assert "Authorization: Bearer <redacted>" in masked


def test_sanitize_for_logging_masks_nested_sensitive_values():
    payload = {
        "headers": {"Authorization": "Bearer super-secret-token"},
        "params": {
            "apikey": "07V86L0YD9URFVSC",
            "symbol": "MSFT",
        },
        "items": ["token=abc123", "safe"],
    }

    sanitized = sanitize_for_logging(payload)

    assert sanitized["headers"]["Authorization"] == "<redacted>"
    assert sanitized["params"]["apikey"] == "<redacted>"
    assert sanitized["params"]["symbol"] == "MSFT"
    assert sanitized["items"][0] == "token=<redacted>"


def test_logging_factory_redacts_sensitive_values_in_log_output(caplog):
    logger = logging.getLogger("tests.logging_safety")

    with caplog.at_level(logging.DEBUG):
        logger.debug(
            "Request failed: %s",
            "https://www.alphavantage.co/query?apikey=07V86L0YD9URFVSC&symbol=MSFT",
        )

    assert "07V86L0YD9URFVSC" not in caplog.text
    assert "apikey=<redacted>" in caplog.text
