# -*- coding: utf-8 -*-

# Python Standard Libraries
from datetime import datetime
from functools import partial
from time import time
import json
import logging
import zlib

# Installed packages (via pip)
from celery import task
from django.contrib.auth.models import User
from django.core.cache import cache
from django.utils.translation import ugettext_noop
from eol_completion import settings
from eol_sso.services.interface import get_user_id_with_indiv_id_list

# Edx dependencies
from common.djangoapps.student.models import CourseAccessRole
from lms.djangoapps.instructor_task.api_helper import submit_task
from lms.djangoapps.instructor_task.tasks_base import BaseInstructorTask
from lms.djangoapps.instructor_task.tasks_helper.runner import run_main_task, TaskProgress
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator
from xmodule.modulestore.django import modulestore

# Internal project dependencies
from .exceptions import CompressionException
from .utils import Content, get_context_big_course, get_ticks

logger = logging.getLogger(__name__)

TIME_CACHE = 300
if hasattr(settings, 'EOL_COMPLETION_TIME_CACHE'):
    TIME_CACHE = settings.EOL_COMPLETION_TIME_CACHE 

@task(base=BaseInstructorTask, queue='edx.lms.core.low')
def process_tick(entry_id, xmodule_instance_args):
    action_name = ugettext_noop('generated')
    task_fn = partial(task_get_tick, xmodule_instance_args)
    return run_main_task(entry_id, task_fn, action_name)

def task_get_tick(
        _xmodule_instance_args,
        _entry_id,
        course_id,
        task_input,
        action_name):
    course_key = course_id
    start_time = time()
    task_progress = TaskProgress(
        action_name,
        1,
        start_time)
    is_bigcourse = task_input["is_bigcourse"] == '1'
    if is_bigcourse:
        data = get_context_big_course(course_key)
    else:
        display_name_course = task_input["display_name"]
        staff_query = CourseAccessRole.objects.filter(
                course_id=course_key
            ).values('user_id')
        enrolled_students = User.objects.filter(
                courseenrollment__course_id=course_key,
                courseenrollment__is_active=1
            ).exclude(
                id__in=staff_query
            ).order_by('username').values('id', 'username', 'email')
        user_id_list = enrolled_students.values_list('id', flat=True)
        user_indiv_id_list = get_user_id_with_indiv_id_list(user_id_list)
        if user_indiv_id_list != []:
            user_indiv_id_dict = {user_id: indiv_id for user_id, indiv_id in user_indiv_id_list}
            for user in enrolled_students:
                indiv_id = user_indiv_id_dict.get(user['id'], '')
                user['indiv_id'] = indiv_id
        store = modulestore()
        data_content = cache.get("eol_completion-" + task_input["course_id"] + "-content")
        if data_content is None:
            info = Content().dump_module(store.get_course(course_key))
            id_course = str(BlockUsageLocator(course_key, "course", "course"))
            if 'i4x://' in id_course:
                id_course = str(
                    BlockUsageLocator(
                        course_key,
                        "course",
                        display_name_course))
            content, max_unit = Content().get_content(info, id_course)
        else:
            # Dictionary with all course blocks
            info = data_content[2]
            content = data_content[0]
            max_unit = data_content[1]
        data = get_ticks(
            content, info, enrolled_students, course_key, max_unit)

    times = datetime.now()
    times = times.strftime("%d/%m/%Y, %H:%M:%S")
    data['time'] = times
    data['is_bigcourse'] = is_bigcourse
    data['time_queue'] = str(TIME_CACHE / 60)
    current_step = {'step': 'Uploading Data Eol Completion'}
    # This was modified to make a comparison with a larger scale of users. Since the matrix will be larger, it is no longer just a processing problem, but rather a memory (caching) storage problem
    try:
        data = zlib.compress(json.dumps(data).encode('utf-8'))
    except (zlib.error, TypeError, ValueError) as e:
        logger.error(f"EolCompletion compress error: {e}")
        raise CompressionException(f"Failed to compress cached data for course_id={course_id}") from e

    cache.set(
        "eol_completion-" +
        task_input["course_id"] +
        "-data",
        data,
        TIME_CACHE)

    return task_progress.update_task_state(extra_meta=current_step)

def task_process_tick(request, course_id, display_name_course, is_bigcourse):
    course_key = CourseKey.from_string(course_id)
    task_type = 'EOL_Completion'
    task_class = process_tick
    task_input = {'course_id': course_id, 'display_name': display_name_course, 'is_bigcourse': is_bigcourse}
    task_key = course_id

    return submit_task(
        request,
        task_type,
        task_class,
        course_key,
        task_input,
        task_key)
