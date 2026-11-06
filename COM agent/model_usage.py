"""Per-request usage metadata only; never record prompts, health text or secrets."""
from contextvars import ContextVar

events = ContextVar('model_usage', default=None)


def record(event):
    current = events.get()
    if current is not None:
        current.append(dict(event))


def usage_fields(usage):
    usage = usage if isinstance(usage, dict) else {}
    def count(value):
        return value if type(value) is int and value >= 0 else None
    details = usage.get('prompt_tokens_details')
    details = details if isinstance(details, dict) else {}
    return {'input_tokens':count(usage.get('input_tokens',usage.get('prompt_tokens'))),
            'output_tokens':count(usage.get('output_tokens',usage.get('completion_tokens'))),
            'cached_input_tokens':count(usage.get('cached_input_tokens',details.get('cached_tokens')))}
