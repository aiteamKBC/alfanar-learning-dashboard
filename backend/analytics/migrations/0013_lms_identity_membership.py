"""Owner-approved links from live-LMS learner accounts to identity groups (LMS learner IDs only)."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0012_retire_legacy_lecture_state'),
    ]

    operations = [
        migrations.CreateModel(
            name='LmsIdentityMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('lms_learner_id', models.BigIntegerField()),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lms_memberships',
                                            to='analytics.identitygroup')),
            ],
        ),
        migrations.AddConstraint(
            model_name='lmsidentitymembership',
            constraint=models.UniqueConstraint(fields=('group', 'lms_learner_id'), name='unique_lms_identity_membership'),
        ),
    ]
