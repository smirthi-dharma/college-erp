"""Reading an uploaded .xlsx safely, and the cell-level cleaners shared by all importers.

The workbook is read with data_only=True, so formulas are never evaluated (only a cached value, if any, is seen); every
cell becomes plain text, so nothing from the file can be executed or injected into a spreadsheet later.
"""
import datetime
import hashlib
import re
import zipfile

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000  # Cloudflare times out long requests; background processing is a follow-up for larger colleges
MAX_UNCOMPRESSED = 60 * 1024 * 1024
MAX_RATIO = 200
FORMULA_STARTS = ('=', '+', '-', '@')


class FileRejected(Exception):
    """The file cannot even be read (wrong type, too big, unsafe)."""


def sha256_of(uploaded):
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    return digest.hexdigest()


def _check_container(uploaded):
    name = (uploaded.name or '').lower()
    if not name.endswith('.xlsx'):
        raise FileRejected('Only .xlsx files are accepted (not .xls, .xlsm or .csv).')
    if uploaded.size > MAX_BYTES:
        raise FileRejected('The file is larger than 5 MB.')
    try:
        archive = zipfile.ZipFile(uploaded)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FileRejected('The file is not a valid .xlsx workbook.') from exc
    with archive:
        names = set(archive.namelist())
        if 'xl/workbook.xml' not in names or '[Content_Types].xml' not in names:
            raise FileRejected('The file is not a valid .xlsx workbook.')
        if any(n.lower().endswith('vbaproject.bin') for n in names):
            raise FileRejected('Workbooks with macros are not accepted.')
        infos = archive.infolist()
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED or any(i.compress_size and i.file_size / i.compress_size > MAX_RATIO for i in infos):
            raise FileRejected('The file expands to an unsafe size and was refused.')
    uploaded.seek(0)


def _text(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, (datetime.datetime, datetime.date)):
        return (value.date() if isinstance(value, datetime.datetime) else value).isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # Excel stores 2101234 as 2101234.0
    return str(value).strip()


def read_workbook(uploaded):
    """Returns (headers, rows) where rows is a list of (sheet_row_number, {header: text}). Raises FileRejected."""
    from openpyxl import load_workbook
    _check_container(uploaded)
    try:
        sheet = load_workbook(uploaded, read_only=True, data_only=True).worksheets[0]
        raw = list(sheet.iter_rows(values_only=True))
    except Exception as exc:
        raise FileRejected('The workbook could not be read.') from exc
    if not raw:
        raise FileRejected('The sheet is empty.')
    headers = [re.sub(r'\s+', '_', _text(h).lower()) for h in raw[0]]
    if not any(headers):
        raise FileRejected('The first row must contain the column headers.')
    rows = []
    for number, cells in enumerate(raw[1:], start=2):
        values = {h: _text(c) for h, c in zip(headers, cells, strict=False) if h}
        if any(values.values()):  # skip fully blank rows
            rows.append((number, values))
    if len(rows) > MAX_ROWS:
        raise FileRejected(f'The file has {len(rows)} rows; the limit is {MAX_ROWS} per file. Split it into several files.')
    if not rows:
        raise FileRejected('The sheet has no data rows.')
    return headers, rows


# --- cell cleaners: each returns (value, error_message) -------------------------------------------------------------

def clean_text(value, maximum, required=False):
    value = (value or '').strip()
    if not value:
        return ('', 'is required') if required else ('', None)
    if len(value) > maximum:
        return value, f'is longer than {maximum} characters'
    return value, None


def clean_email(value, required=True):
    value = (value or '').strip().lower()
    if not value:
        return ('', 'is required') if required else ('', None)
    if len(value) > 254:
        return value, 'is too long'
    try:
        validate_email(value)
    except ValidationError:
        return value, 'is not a valid e-mail address'
    return value, None


def clean_gender(value):
    value = (value or '').strip().lower()
    mapping = {'m': 'M', 'male': 'M', 'f': 'F', 'female': 'F', '': ''}
    if value not in mapping:
        return value, 'must be M or F'
    return mapping[value], None


PHONE_RE = re.compile(r'^[6-9]\d{9}$')


def normalize_phone(value):
    """10-digit Indian mobile numbers (also written +91 / 91 / 0 prefixed) -> +91XXXXXXXXXX, else None."""
    digits = re.sub(r'[\s\-().]', '', value or '')
    if digits.startswith('+'):
        digits = digits[1:]
        if not digits.startswith('91'):
            return None
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    if not digits.isdigit() or not PHONE_RE.match(digits):
        return None
    return '+91' + digits


def clean_phone(value, required=False):
    value = (value or '').strip()
    if not value:
        return ('', 'is required') if required else ('', None)
    phone = normalize_phone(value)
    if phone is None:
        return value, 'is not a valid 10-digit Indian mobile number'
    return phone, None


DOB_FORMATS = ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y')


def clean_dob(value):
    value = (value or '').strip()
    if not value:
        return None, None
    for fmt in DOB_FORMATS:
        try:
            day = datetime.datetime.strptime(value, fmt).date()
            break
        except ValueError:
            continue
    else:
        return None, 'must be a date like 2006-04-23 or 23/04/2006'
    today = datetime.date.today()
    if day > today:
        return None, 'cannot be in the future'
    if day > today.replace(year=today.year - 3):
        return None, 'cannot be less than 3 years ago'
    return day, None
