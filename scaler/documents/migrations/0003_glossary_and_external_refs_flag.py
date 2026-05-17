import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0002_financialrisk_riskfinding'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='include_external_references',
            field=models.BooleanField(default=True),
        ),
        migrations.CreateModel(
            name='GlossaryEntry',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('term', models.CharField(db_index=True, max_length=255)),
                ('definition', models.TextField()),
                ('context', models.TextField(blank=True)),
                ('clause_ids', models.JSONField(default=list)),
                ('external_references', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='glossary_entries', to='documents.document')),
            ],
            options={
                'ordering': ['document', 'term'],
            },
        ),
        migrations.AddConstraint(
            model_name='glossaryentry',
            constraint=models.UniqueConstraint(fields=('document', 'term'), name='unique_term_per_document'),
        ),
    ]
