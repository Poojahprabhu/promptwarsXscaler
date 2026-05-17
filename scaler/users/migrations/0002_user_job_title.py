from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="job_title",
            field=models.CharField(
                blank=True,
                default="",
                max_length=100,
                verbose_name="Job Title",
            ),
            preserve_default=False,
        ),
    ]
