"""Authenticated on-demand directory downloads; no source or database writes."""
import csv
import io

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from . import metrics

# Excel titles must fit 31 characters; the full approved name is in every data row.
MODULE_SHEETS = {83334: 'Planning', 121642: 'Procurement Risk', 135945: 'Operations', 154268: 'Advanced'}
HEADERS = (
    'Stable learner ID', 'Learner name', 'Module name', 'LMS Progress (%)',
    'Attendance evidence (%)', 'Quizzes attempted', 'Total available quizzes',
    'Quiz completion (%)', 'Average Quiz Score (%)', 'Materials completed',
    'Total applicable materials', 'Materials completion (%)',
    'Assignments submitted', 'Total available assignments', 'Total verified Teams attendance duration',
)
PERCENT_COLUMNS = (4, 5, 8, 9, 12)


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


def values(row):
    return [row['learner_id'], row['name'], row['enrollments'][0]['course'],
        row['mean_progress'], row['attendance']['pct'], row['quizzes_attempted'],
        row['quizzes_known'] or None, row['quiz_completion_pct'], row['mean_latest_score'],
        row['lessons_completed'], row['lessons_known'] or None, row['lessons_completion_pct'],
        row['assignments_submitted'], row['assignments_known'] or None,
        row['verified_attendance_seconds']]


def module_export(course_ids, file_type):
    # Same scoped rows as the screen. Always all enrolled learners, not a paginated/search subset.
    tables = [(MODULE_SHEETS[cid], metrics.learner_directory(course_id=cid)) for cid in course_ids]
    if file_type == 'csv':
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(HEADERS)
        for row in tables[0][1]:
            cells = values(row)
            cells[-1] = attendance_label(cells[-1])
            writer.writerow([safe_csv_text('N/A' if v is None else v) for v in cells])
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
                seconds = cells[-1]
                # Native numeric Excel duration, preserving exact verified seconds.
                cells[-1] = seconds / 86400 if seconds is not None else None
                sheet.append(['N/A' if v is None else v for v in cells])
                for cell in sheet[sheet.max_row]:
                    if isinstance(cell.value, str):
                        cell.data_type = 's'  # untrusted names remain literal, never formulas
                    cell.alignment = Alignment(vertical='center', wrap_text=cell.column in (2, 3))
                sheet.row_dimensions[sheet.max_row].height = 32
                for column in PERCENT_COLUMNS:
                    sheet.cell(sheet.max_row, column).number_format = '0.0"%"'
                sheet.cell(sheet.max_row, len(HEADERS)).number_format = (
                    '"<1m"' if seconds is not None and 0 < seconds < 60 else '[h]"h" mm"m"')
            for column in sheet.columns:
                letter = column[0].column_letter
                sheet.column_dimensions[letter].width = 44 if letter == 'C' else 30 if letter in ('B', 'O') else 21
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
