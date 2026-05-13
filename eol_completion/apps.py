# -*- coding: utf-8 -*-


from django.apps import AppConfig
from openedx.core.djangoapps.plugins.constants import PluginSettings, PluginURLs, ProjectType, SettingsType
import logging, time
from celery.signals import task_prerun, task_postrun

logger = logging.getLogger(__name__)
_task_start = {}


class EolCompletionConfig(AppConfig):
    name = 'eol_completion'

    plugin_app = {
        PluginURLs.CONFIG: {
            ProjectType.LMS: {
                PluginURLs.NAMESPACE: '',
                PluginURLs.REGEX: r'^',
                PluginURLs.RELATIVE_PATH: 'urls',
            }},
        PluginSettings.CONFIG: {
            ProjectType.CMS: {
                SettingsType.COMMON: {
                    PluginSettings.RELATIVE_PATH: 'settings.common'},
            },
            ProjectType.LMS: {
                SettingsType.COMMON: {
                    PluginSettings.RELATIVE_PATH: 'settings.common'},
            },
        }}

    def ready(self):
        # hook Celery signals (esto sería para medir el tiempo de la task)
        @task_prerun.connect
        def _on_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **kw):
            _task_start[task_id] = time.perf_counter()

        @task_postrun.connect
        def _on_postrun(sender=None, task_id=None, task=None, args=None, kwargs=None,
                        retval=None, state=None, **kw):
            t0 = _task_start.pop(task_id, None)
            if t0 is not None and getattr(sender, "__name__", "") in ("process_tick",):
                dt = time.perf_counter() - t0
                # Quizás deberíamos habilitar el log solo en debug.
                logger.info("EolCompletion timing | Celery postrun: %.6f s | task_id=%s state=%s", dt, task_id, state)
