from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("analytics", "0008_transcript_speaking_matches")]
    operations = [
        migrations.RemoveConstraint(model_name="lectureresource", name="unique_lecture_library_resource"),
        migrations.AddField(model_name="lectureresource", name="provider", field=models.CharField(choices=[("teams", "Teams"), ("onedrive", "OneDrive")], default="teams", max_length=15)),
        migrations.AddField(model_name="lectureresource", name="drive_id", field=models.CharField(blank=True, max_length=300)),
        migrations.AddConstraint(model_name="lectureresource", constraint=models.UniqueConstraint(fields=("lecture", "provider", "kind", "source_id"), name="unique_lecture_library_resource")),
    ]
