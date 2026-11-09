"""Authenticated on-demand directory downloads; no source or database writes."""
import csv
import datetime as dt
import io

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from . import metrics

# Excel titles must fit 31 characters; the full approved name is in every data row.
MODULE_SHEETS = {83334: 'Planning', 121642: 'Procurement Risk', 135945: 'Operations', 154268: 'Advanced'}
HEADERS = (
    'Stable learner ID', 'Learner name', 'Module name', 'Mean LMS Progress (%)',
    'Attendance evidence (%)', 'Quizzes attempted', 'Total available quizzes',
    'Quiz completion (%)', 'Average Quiz Score (%)', 'Materials completed',
    'Total applicable materials', 'Materials completion (%)',
    'Assignments submitted', 'Total available assignments', 'Total verified Teams attendance duration',
    'Last Activity (UTC)', 'Attendance present observations', 'Attendance known observations',
    'Latest quizzes passed', 'Latest quizzes graded',
)
PERCENT_COLUMNS = (4, 5, 8, 9, 12)
DURATION_COLUMN = 15
LAST_ACTIVITY_COLUMN = 16
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec')


def attendance_label(seconds):
    if seconds is None:
        return 'N/A'
    if 0 < seconds < 60:
        return '<1m'
    return f'{int(seconds // 3600)}h {int(seconds // 60 % 60)}m'


def safe_csv_text(value):
    # Quoting alone does not stop spreadsheet formulas. Also guard leading controls/space.
    if isinstance(value, str) and (value.startswith(('\t', '\r', '\n')) or
            value.lstrip().startswith(('=', '+', '-', '@'))):
        return "'" + value
    return value


def last_activity_utc(value):
    """Keep the directory timestamp; Excel cannot store timezone-aware datetimes."""
    if value is None:
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None)


def last_activity_label(value):
    if value is None:
        return 'No recorded activity'
    return f'{value.day:02d} {MONTHS[value.month - 1]} {value.year}'


def values(row):
    """Serialize only authoritative directory fields; never calculate replacement metrics."""
    return [row['learner_id'], row['name'], row['enrollments'][0]['course'],
        row['mean_progress'], row['attendance']['pct'], row['quizzes_attempted'],
        row['quizzes_known'] or None, row['quiz_completion_pct'], row['mean_latest_score'],
        row['lessons_completed'], row['lessons_known'] or None, row['lessons_completion_pct'],
        row['assignments_submitted'] if row['assignments_known'] else None, row['assignments_known'] or None,
        row['verified_attendance_seconds'], last_activity_utc(row['last_activity']),
        row['attendance']['attended'], row['attendance']['expected'],
        row['quizzes_passed_latest'] if row['quizzes_graded'] else None, row['quizzes_graded'] or None]


def missing_label(column):
    # Preserve the existing directory's distinct missing-data labels.
    if column == 4:
        return '\u2014'
    if column in (5, 17, 18):
        return 'Not Available'
    if column == LAST_ACTIVITY_COLUMN:
        return 'No recorded activity'
    return 'N/A'


def module_export(course_ids, file_type):
    # Same scoped rows as the screen. Always all enrolled learners, not a paginated/search subset.
    tables = [(MODULE_SHEETS[cid], metrics.learner_directory(course_id=cid)) for cid in course_ids]
    if file_type == 'csv':
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(HEADERS)
        for row in tables[0][1]:
            cells = values(row)
            cells[DURATION_COLUMN - 1] = attendance_label(cells[DURATION_COLUMN - 1])
            cells[LAST_ACTIVITY_COLUMN - 1] = last_activity_label(cells[LAST_ACTIVITY_COLUMN - 1])
            writer.writerow([safe_csv_text(missing_label(i) if v is None else v) for i, v in enumerate(cells, 1)])
        content = output.getvalue().encode('utf-8-sig')
        mime = 'text/csv; charset=utf-8'
    else:
        workbook = Workbook()
        workbook.remove(workbook.active)
        for title, rows in tables:
            sheet = workbook.create_sheet(title)
            sheet.append(HEADERS)
            sheet.freeze_panes = 'C2'
            for cell in sheet[1]:
                cell.font = Font(bold=True, color='FFFFFF')
                cell.fill = PatternFill('solid', fgColor='142C52')
                cell.alignment = Alignment(wrap_text=True, vertical='center')
            sheet.row_dimensions[1].height = 44
            for row in rows:
                cells = values(row)
                seconds = cells[DURATION_COLUMN - 1]
                # Native numeric Excel duration, preserving exact verified seconds.
                cells[DURATION_COLUMN - 1] = seconds / 86400 if seconds is not None else None
                sheet.append([missing_label(i) if v is None else v for i, v in enumerate(cells, 1)])
                for cell in sheet[sheet.max_row]:
                    if isinstance(cell.value, str):
                        cell.data_type = 's'  # untrusted names remain literal, never formulas
                    cell.alignment = Alignment(vertical='center', wrap_text=cell.column in (2, 3))
                sheet.row_dimensions[sheet.max_row].height = 32
                for column in PERCENT_COLUMNS:
                    cell = sheet.cell(sheet.max_row, column)
                    cell.number_format = '0"%"' if isinstance(cell.value, (int, float)) and float(cell.value).is_integer() else '0.0"%"'
                sheet.cell(sheet.max_row, DURATION_COLUMN).number_format = (
                    '"<1m"' if seconds is not None and 0 < seconds < 60 else '[h]"h" m"m"')
                date = cells[LAST_ACTIVITY_COLUMN - 1]
                if date is not None:
                    # Same en-GB month labels as the directory, regardless of Excel locale.
                    sheet.cell(sheet.max_row, LAST_ACTIVITY_COLUMN).number_format = f'dd "{MONTHS[date.month - 1]}" yyyy'
            for column in sheet.columns:
                letter = column[0].column_letter
                sheet.column_dimensions[letter].width = 44 if letter == 'C' else 30 if letter in ('B', 'O', 'P') else 21
            sheet.auto_filter.ref = sheet.dimensions
            sheet.sheet_view.showGridLines = False
        output = io.BytesIO()
        workbook.save(output)
        content, mime = output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    response = HttpResponse(content, content_type=mime)
    response['Content-Disposition'] = f'attachment; filename="alfanar-learners-{timezone.now():%Y%m%d-%H%M%S}.{file_type}"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
