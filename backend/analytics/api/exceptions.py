from rest_framework.views import exception_handler


def json_exception_handler(exc, context):
    """Wrap DRF errors in a consistent {"error": {...}} envelope."""
    response = exception_handler(exc, context)
    if response is not None:
        detail = response.data
        message = detail.get("detail") if isinstance(detail, dict) and "detail" in detail else "Request failed"
        response.data = {"error": {"status": response.status_code, "message": str(message), "details": detail}}
    return response
