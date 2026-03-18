from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinical', '0002_alter_document_client_alter_sessionnote_client_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='cloudinary_public_id',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
