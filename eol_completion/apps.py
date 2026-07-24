# -*- coding: utf-8 -*-

# Python Standard Libraries
import logging, time

# Installed packages (via pip)
from celery.signals import task_prerun, task_postrun
from django.apps import AppConfig

# Edx dependencies
from openedx.core.djangoapps.plugins.constants import PluginSettings, PluginURLs, ProjectType, SettingsType

# Internal project dependencies

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
        @task_prerun.connect(weak=False)
        def on_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **kw):
            """Records the start time of a task when it begins execution."""
            task_name = getattr(sender, "__name__", "unknown")
            logger.info("EolCompletion timing | Celery prerun: task=%s task_id=%s", task_name, task_id)
            _task_start[task_id] = time.perf_counter()

        @task_postrun.connect(weak=False)
        def on_postrun(sender=None, task_id=None, task=None, args=None, kwargs=None, retval=None, state=None, **kw):
            """Logs the elapsed execution time of a task once it finishes."""
            t0 = _task_start.pop(task_id, None)
            task_name = getattr(sender, "__name__", "unknown")
            if t0 is not None:
                dt = time.perf_counter() - t0
                logger.info("EolCompletion timing | Celery postrun: %.6f s | task=%s task_id=%s state=%s", dt, task_name, task_id, state)
