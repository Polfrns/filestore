import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Fresh-install migration — creates kb_kbindex from scratch.

    For existing dev DBs migrating from the legacy app, run first:
        python manage.py dbshell < scratchpad/migrate-legacy-to-kb.sql
        python manage.py migrate kb 0001 --fake
    """

    initial = True

    dependencies = [
        ('filestores', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='KBIndex',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_path', models.TextField()),
                ('file_mtime', models.FloatField()),
                ('ingested_at', models.DateTimeField(auto_now=True)),
                ('filestore', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='kb_entries',
                    to='filestores.filestore',
                )),
            ],
            options={
                'unique_together': {('filestore', 'file_path')},
            },
        ),
    ]
