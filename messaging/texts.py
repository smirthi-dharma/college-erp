"""Local copies of the approved template bodies, used only to show a dry run and the report. Meta sends its own text."""
BODIES = {
    'en': ('Dear {1}, on {2} the following were marked absent: {3}. '
           'For any query please contact the college office. - {4}'),
    # Tamil wording is a draft: a native speaker must check it before it is submitted to Meta (open question Q8).
    'ta': ('அன்புள்ள {1}, {2} அன்று பின்வருபவர்கள் வருகை தரவில்லை எனப் பதிவாகியுள்ளது: {3}. '
           'ஏதேனும் விசாரணைக்கு கல்லூரி அலுவலகத்தைத் தொடர்பு கொள்ளவும். - {4}'),
}
MORE = {
    'en': ' and {n} more, please contact the college',
    'ta': ' மேலும் {n} பேர், கல்லூரியைத் தொடர்பு கொள்ளவும்',
}
PERIOD = {'en': 'P{n}', 'ta': 'P{n}'}
STOP_WORDS = {'stop', 'unsubscribe', 'நிறுத்து', 'நிறுத்துக'}


def render(language, params):
    text = BODIES[language]
    for index, value in enumerate(params, start=1):
        text = text.replace('{%d}' % index, value)
    return text
