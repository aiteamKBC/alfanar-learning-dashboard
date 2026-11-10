"""Retain legacy SQLite rows, detach only their foreign keys to the live registry."""
from django.db import migrations, models


def detach_registry_constraints(apps, schema_editor):
    connection = schema_editor.connection
    if connection.alias != 'default' or connection.vendor != 'sqlite':
        return
    tables = set(connection.introspection.table_names())
    for model_name, field_name in [('LectureCapture', 'course'), ('LectureLearner', 'learner')]:
        model = apps.get_model('analytics', model_name)
        if model._meta.db_table not in tables:
            continue
        old = model._meta.get_field(field_name)
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
        if not any(c.get('foreign_key') and old.column in c['columns'] for c in constraints.values()):
            continue
        # Same column name, nullability and integer values. SQLite rebuilds the
        # table to remove its FK, so retain a backup and verify rows in rehearsal.
        new = models.BigIntegerField(db_column=old.column, null=old.null)
        new.set_attributes_from_name(field_name)
        new.model = model
        schema_editor.alter_field(model, old, new, strict=True)


class Migration(migrations.Migration):
    dependencies = [('analytics', '0011_merge_lecture_library_and_owner_identity')]
    operations = [
        migrations.RunPython(detach_registry_constraints, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.DeleteModel('LectureResource'),
            migrations.DeleteModel('LectureLearner'),
            migrations.DeleteModel('LectureCapture'),
        ]),
    ]
