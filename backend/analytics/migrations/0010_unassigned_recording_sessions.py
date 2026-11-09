from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("analytics", "0009_lecture_resource_provider")]
    operations = [migrations.AlterField(
        model_name="lecturecapture", name="course",
        field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                related_name="lecture_captures", to="analytics.course"),
    )]
