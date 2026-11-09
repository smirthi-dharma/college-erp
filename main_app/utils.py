import json

from django.utils.safestring import mark_safe


def js_json(value):
    """JSON for embedding inside a <script> block, safe against </script> and HTML injection.

    The inherited dashboards printed Python lists with ``|safe``, so a student who set their
    first name to ``'];alert(1);//`` ran script in the principal's browser.
    """
    text = json.dumps(value)
    return mark_safe(  # noqa: S308  # nosec B308 B703 - JSON-encoded, with <, > and & escaped
        text.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
                     .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
