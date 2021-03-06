# Generated by Django 2.2 on 2019-05-15 15:17

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stechec', '0003_tournament_authors'),
    ]

    operations = [
        migrations.AddField(
            model_name='tournament',
            name='visible',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='tournamentplayer',
            name='champion',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tournamentplayers', to='stechec.Champion', verbose_name='champion'),
        ),
        migrations.AlterField(
            model_name='tournamentplayer',
            name='tournament',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tournamentplayers', to='stechec.Tournament', verbose_name='tournoi'),
        ),
    ]
