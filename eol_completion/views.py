# -*- coding: utf-8 -*-

# Python Standard Libraries
import logging
import six
from collections import OrderedDict, defaultdict, deque
from datetime import datetime
from functools import partial
from time import time
from time import perf_counter
import json
import zlib

# Installed packages (via pip)
from celery import task
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Max
from django.http import Http404, JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import ugettext_noop
from django.views.generic.base import View
import numpy as np
from pytz import UTC
from eol_sso.services.interface import get_user_id_with_indiv_id_list

# Edx dependencies
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor_task.api_helper import AlreadyRunningError, submit_task
from lms.djangoapps.instructor_task.tasks_base import BaseInstructorTask
from lms.djangoapps.instructor_task.tasks_helper.runner import run_main_task, TaskProgress
from opaque_keys.edx.keys import CourseKey, UsageKey, LearningContextKey
from opaque_keys.edx.locator import BlockUsageLocator
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from web_fragments.fragment import Fragment
from xblock_discussion import DiscussionXBlock
from xblock.fields import Scope
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.inheritance import own_metadata

# Internal project dependencies
from completion.models import BlockCompletion
import logging

from django.db.models import Count, Q
from django.db import connection
from itertools import islice

def _t_start():
    return perf_counter()

def _t_log(label, t0, extra=None):
    dt = perf_counter() - t0
    if extra is None:
        logger.info("EolCompletion timing | %s: %.6f s", label, dt)
    else:
        logger.info("EolCompletion timing | %s: %.6f s | %s", label, dt, extra)

logger = logging.getLogger(__name__)
FILTER_LIST = ['xml_attributes']
INHERITED_FILTER_LIST = ['children', 'xml_attributes']
LIMIT_STUDENTS = 10000
TIME_CACHE  = 300
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
    start_date = datetime.now(UTC)
    task_progress = TaskProgress(
        action_name,
        1,
        start_time)
    is_bigcourse = task_input["is_bigcourse"] == '1'
    if is_bigcourse:
        data = EolCompletionData().get_context_big_course(course_key)
    else:
        display_name_course = task_input["display_name"]
        enrolled_students = User.objects.filter(
                courseenrollment__course_id=course_key,
                courseenrollment__is_active=1,
                courseenrollment__mode='honor'
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
        data = EolCompletionData().get_ticks(
            content, info, enrolled_students, course_key, max_unit)

    times = datetime.now()
    times = times.strftime("%d/%m/%Y, %H:%M:%S")
    data['time'] = times
    data['is_bigcourse'] = is_bigcourse
    data['time_queue'] = str(TIME_CACHE / 60)
    current_step = {'step': 'Uploading Data Eol Completion'}

    # modifiqué esto para hacer una comparación con un nivel más grande de usuarios. Dado que la matriz será más grande, deja de ser un problema solo de procesamiento y pasa a ser de almacenamiento en memoria (en caché).
    try:
        payload = zlib.compress(json.dumps(data).encode('utf-8'))
    except Exception as e:
        logger.error(f"EolCompletion compress error: {e}")
        payload = data

    cache.set(
        "eol_completion-" +
        task_input["course_id"] +
        "-data",
        payload,
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


class Content(object):
    def get_content(self, info, id_course):
        """
            Returns dictionary of ordered sections, subsections and units
        """
        max_unit = 0   # Number of units in all sections
        content = OrderedDict()
        children_course = info[id_course]
        children_course = children_course['children']  # All course sections
        children = 0  # Number of units per section
        for id_section in children_course:  # Iterate each section
            section = info[id_section]
            aux_name_sec = section['metadata']
            children = 0
            content[id_section] = {
                'type': 'section',
                'name': aux_name_sec['display_name'],
                'id': id_section,
                'num_children': children}
            subsections = section['children']
            for id_subsection in subsections:  # Iterate each subsection
                subsection = info[id_subsection]
                units = subsection['children']
                aux_name = subsection['metadata']
                len_unit = len(units)
                content[id_subsection] = {
                    'type': 'subsection',
                    'name': aux_name['display_name'],
                    'id': id_subsection,
                    'num_children': 0}
                for id_uni in units:  # Iterate each unit and get unit name
                    unit = info[id_uni]
                    if len(unit['children']) > 0:
                        max_unit += 1
                        content[id_uni] = {
                            'type': 'unit',
                            'name': unit['metadata']['display_name'],
                            'id': id_uni}
                    else:
                        len_unit -= 1
                children += len_unit
                content[id_subsection]['num_children'] = len_unit
            content[id_section] = {
                'type': 'section',
                'name': aux_name_sec['display_name'],
                'id': id_section,
                'num_children': children}

        return content, max_unit

    def dump_module(
            self,
            module,
            destination=None,
            inherited=False,
            defaults=False):
        """
        Add the module and all its children to the destination dictionary in
        as a flat structure.
        """

        destination = destination if destination else {}

        items = own_metadata(module)

        # HACK: add discussion ids to list of items to export (AN-6696)
        if isinstance(
                module,
                DiscussionXBlock) and 'discussion_id' not in items:
            items['discussion_id'] = module.discussion_id

        filtered_metadata = {
            k: v for k,
            v in six.iteritems(items) if k not in FILTER_LIST}

        destination[six.text_type(module.location)] = {
            'category': module.location.block_type,
            'children': [six.text_type(child) for child in getattr(module, 'children', [])],
            'metadata': filtered_metadata,
        }

        if inherited:
            # When calculating inherited metadata, don't include existing
            # locally-defined metadata
            inherited_metadata_filter_list = list(filtered_metadata.keys())
            inherited_metadata_filter_list.extend(INHERITED_FILTER_LIST)

            def is_inherited(field):
                if field.name in inherited_metadata_filter_list:
                    return False
                elif field.scope != Scope.settings:
                    return False
                elif defaults:
                    return True
                else:
                    return field.values != field.default

            inherited_metadata = {field.name: field.read_json(
                module) for field in list(module.fields.values()) if is_inherited(field)}
            destination[six.text_type(
                module.location)]['inherited_metadata'] = inherited_metadata

        for child in module.get_children():
            self.dump_module(child, destination, inherited, defaults)

        return destination


class EolCompletionFragmentView(EdxFragmentView, Content):
    def render_to_fragment(self, request, course_id, **kwargs):
        course_key = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, "load", course_key)

        staff_access = bool(has_access(request.user, 'staff', course))
        data_researcher_access = request.user.has_perm(permissions.CAN_RESEARCH, course_key)
        if not (staff_access or data_researcher_access):
            raise Http404()
        limit_student = LIMIT_STUDENTS
        if hasattr(settings, 'EOL_COMPLETION_LIMIT_STUDENT'):
            limit_student = settings.EOL_COMPLETION_LIMIT_STUDENT 
        is_big = limit_student < User.objects.filter(
            courseenrollment__course_id=course_key,
            courseenrollment__is_active=1,
            courseenrollment__mode='honor'
        ).count()
        if not is_big:
            context = self.get_context(request, course_id, course, course_key)
            html = render_to_string(
            'eol_completion/eol_completion_fragment.html', context)
        else:
            context = self.get_context_big_course(course, course_key)
            html = render_to_string(
                'eol_completion/eol_completion_bigcourse.html', context)
        fragment = Fragment(html)
        return fragment

    def get_context(self, request, course_id, course, course_key):
        """
            Returns headers table
        """
        data = cache.get("eol_completion-" + course_id + "-content")
        if data is None:
            store = modulestore()
            # Dictionary with all course blocks
            # verificar si hay contenido
            info = self.dump_module(store.get_course(course_key))

            id_course = str(BlockUsageLocator(course_key, "course", "course"))
            if 'i4x://' in id_course:
                id_course = str(
                    BlockUsageLocator(
                        course_key,
                        "course",
                        course.display_name))
            data = []
            content, maxn = self.get_content(info, id_course)

            data.extend([content])
            data.extend([maxn])
            data.extend([info])
            cache.set("eol_completion-" + course_id + "-content", data, TIME_CACHE)

        context = {
            "course": course,
            'page_url': reverse(
                'completion_view',
                kwargs={
                    'course_id': six.text_type(course_key)}),
            'data_url': '{}?is_bigcourse=0'.format(reverse(
                'completion_data_view',
                kwargs={
                    'course_id': six.text_type(course_key)})),
            "content": data[0],
            "max_unit": data[1]}

        return context

    def get_context_big_course(self, course, course_key):
        """
            Return data url for big course
        """
        context = {
            "course": course,
            'page_url': reverse(
                'completion_view',
                kwargs={
                    'course_id': six.text_type(course_key)}),
            'data_url': '{}?is_bigcourse=1'.format(reverse(
                'completion_data_view',
                kwargs={
                    'course_id': six.text_type(course_key)})),
            }
        return context

class EolCompletionData(View, Content):
    @transaction.non_atomic_requests
    def dispatch(self, args, **kwargs):
        return super(EolCompletionData, self).dispatch(args, **kwargs)

    def get(self, request, course_id, **kwargs):
        t0 = _t_start()
        is_bigcourse = request.GET.get('is_bigcourse', None)
        if is_bigcourse is None or is_bigcourse not in ['1','0']:
            logger.error('EolCompletion - Error params, is_bigcourse is not defined or is wrong, is_bigcourse={}'.format(is_bigcourse))
            raise Http404()
        course_key = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, "load", course_key)
        display_name_course = course.display_name
        staff_access = bool(has_access(request.user, 'staff', course))
        data_researcher_access = request.user.has_perm(permissions.CAN_RESEARCH, course_key)
        if not (staff_access or data_researcher_access):
            raise Http404()
        ctx_t0 = _t_start()
        context = self.get_context(request, course_id, display_name_course, is_bigcourse)
        _t_log("EolCompletionData.get_context", ctx_t0, extra=f"big={is_bigcourse}")
        _t_log("EolCompletionData.get total", t0)
        return JsonResponse(context)

    def get_context(self, request, course_id, display_name_course, is_bigcourse):
        """
            Return eol completion data
        """
        cached_data = cache.get("eol_completion-" + course_id + "-data")
        data = None
        if cached_data is not None:
            if isinstance(cached_data, bytes):
                try:
                    data = json.loads(zlib.decompress(cached_data).decode('utf-8'))
                except Exception as e:
                    logger.error(f"EolCompletion decompress error: {e}")
            else:
                data = cached_data

        if data is None:
            data = {"data": [[False]]}
            try:
                task_process_tick(request, course_id, display_name_course, is_bigcourse)
            except AlreadyRunningError:
                pass
        context = data

        return context

    def get_context_big_course(self, course_key):
        """
            Return eol completion data
        """
        context_key = LearningContextKey.from_string(str(course_key))
        aux_block_completions = BlockCompletion.objects.filter(context_key=context_key).values('user').annotate(last_completed=Max('modified'))
        last_block_completions= {}
        for x in aux_block_completions:
            last_block_completions[x['user']] = x['last_completed']
        enrolled_students = User.objects.filter(
                courseenrollment__course_id=course_key,
                courseenrollment__is_active=1,
                courseenrollment__mode='honor'
            ).order_by('username').values('id', 'username', 'email', 'last_login')
        user_id_list = enrolled_students.values_list('id', flat=True)
        user_indiv_id_list = get_user_id_with_indiv_id_list(user_id_list)
        if user_indiv_id_list != []:
            user_indiv_id_dict = {id: indiv_id for id, indiv_id in user_indiv_id_list}
            for user in enrolled_students:
                indiv_id = user_indiv_id_dict.get(user['id'], '')
                user['indiv_id'] = indiv_id
        context = [
            [x['username'], 
            x['indiv_id'], 
            x['email'], 
            last_block_completions[x['id']].strftime("%d/%m/%Y, %H:%M:%S") if x['id'] in last_block_completions else '', 
            x['last_login'].strftime("%d/%m/%Y, %H:%M:%S") if x['last_login'] else ''] for x in enrolled_students
        ]
        if len(context) == 0:
            context = [[True]]
        return {'data': context}

    def get_ticks(
            self,
            content,
            info,
            enrolled_students,
            course_key,
            max_unit):
        """
            Dictionary of students with ticks if students completed the units
        """
        user_tick = defaultdict(list)

        students_id = [x['id'] for x in enrolled_students]
        students_username = [x['username'] for x in enrolled_students]
        students_email = [x['email'] for x in enrolled_students]
        students_indiv_id = [x['indiv_id'] for x in enrolled_students]
        i = 0
        certificate = self.get_certificate(students_id, course_key)
        blocks = self.get_block(students_id, course_key)
        completion = []
        for user in students_id:
            i += 1
            # Get a list of true/false if they completed the units
            # and number of completed units
            data, aux_completion = self.get_data_tick(content, info, user, blocks, max_unit)
            aux_user_tick = deque(data)
            aux_user_tick.appendleft(students_indiv_id[i - 1] if students_indiv_id[i - 1] != None else '')
            aux_user_tick.appendleft(students_username[i - 1])
            aux_user_tick.appendleft(students_email[i - 1])
            aux_user_tick.append('Si' if user in certificate else 'No')
            user_tick['data'].append(list(aux_user_tick))
            if user in certificate:
                aux_completion.append(1)
            else:
                aux_completion.append(0)
            if len(completion) != 0:
                completion = np.sum([completion, aux_completion], 0)
            else:
                completion = aux_completion
        completion = [str(x) for x in completion]
        user_tick['completion'] = completion
        if len(students_id) == 0:
            user_tick['data'] = [[True]]
        return user_tick

    def get_block(self, students_id, course_key):
        """
            Get all completed students block
        """
        context_key = LearningContextKey.from_string(str(course_key))
        aux_blocks = BlockCompletion.objects.filter(
            user_id__in=students_id,
            context_key=context_key,
            completion=1.0).values(
            'user_id',
            'block_key')
        blocks = defaultdict(list)
        for b in aux_blocks:
            blocks[b['user_id']].append(b['block_key'])

        return blocks

    def get_data_tick(self, content, info, user, blocks, max_unit):
        """
            Get a list of true/false if they completed the units
            and number of completed units
        """
        data = []
        aux_completion = []
        completed_unit = 0  # Number of completed units per student
        completed_unit_per_section = 0  # Number of completed units per section
        num_units_section = 0  # Number of units per section
        first = True
        for unit in list(content.items()):
            if unit[1]['type'] == 'unit':
                unit_info = info[unit[1]['id']]
                blocks_unit = unit_info['children']
                if len(blocks_unit) > 0:
                    blocks_unit = [UsageKey.from_string(
                        x) for x in blocks_unit if 'discussion+block' not in x]
                    checker = self.get_block_tick(blocks_unit, blocks, user)
                    completed_unit_per_section += 1
                    num_units_section += 1
                    completed_unit += 1

                if not checker:
                    completed_unit -= 1
                    completed_unit_per_section -= 1
                    data.append('')
                    aux_completion.append(0)
                else:
                    data.append('&#10004;')
                    aux_completion.append(1)
            if not first and unit[1]['type'] == 'section' and unit[1]['num_children'] > 0:
                aux_point = str(completed_unit_per_section) + \
                    "/" + str(num_units_section)
                data.append(aux_point)
                if completed_unit_per_section == num_units_section:
                    aux_completion.append(1)
                else:
                    aux_completion.append(0)
                completed_unit_per_section = 0
                num_units_section = 0
            if first and unit[1]['type'] == 'section' and unit[1]['num_children'] > 0:
                first = False
        aux_point = str(completed_unit_per_section) + \
            "/" + str(num_units_section)
        data.append(aux_point)
        if completed_unit_per_section == num_units_section and num_units_section > 0:
            aux_completion.append(1)
        else:
            aux_completion.append(0)
        aux_final_point = str(completed_unit) + "/" + str(max_unit)
        if completed_unit == max_unit and max_unit > 0:
            aux_completion.append(1)
        else:
            aux_completion.append(0)
        data.append(aux_final_point)
        return data, aux_completion

    def get_block_tick(self, blocks_unit, blocks, user):
        """
            Check if unit block is completed
        """
        if all(elem in blocks[user] for elem in blocks_unit):
            return True
        return False

    def get_certificate(self, students_id, course_id):
        """
            Check if users has generated a certificate
        """
        certificates = GeneratedCertificate.objects.filter(status='downloadable',
            user_id__in=students_id, course_id=course_id).values("user_id")
        cer_students_id = [x["user_id"] for x in certificates]

        return cer_students_id

## FAST IMPLEMENTATION
@task(base=BaseInstructorTask, queue='edx.lms.core.low')
def process_tick_fast(entry_id, xmodule_instance_args):
    action_name = ugettext_noop('generated')
    task_fn = partial(task_get_tick_fast, xmodule_instance_args)
    return run_main_task(entry_id, task_fn, action_name)

def task_get_tick_fast(
        _xmodule_instance_args,
        _entry_id,
        course_id,
        task_input,
        action_name):
    course_key = course_id
    start_time = time()
    start_date = datetime.now(UTC)
    task_progress = TaskProgress(
        action_name,
        1,
        start_time)
    is_bigcourse = task_input["is_bigcourse"] == '1'
    if is_bigcourse:
        # If the number of students exceeds the `settings.EOL_COMPLETION_LIMIT_STUDENT` variable, it is referred to as a big_course and the data is easier to retrieve. The previous implementation of `get_context_big_course` was retained, as it does not encounter any major issues with this.
        data = EolCompletionDataFast().get_context_big_course(course_key)
    else:
        display_name_course = task_input["display_name"]
        enrolled_students = User.objects.filter(
                courseenrollment__course_id=course_key,
                courseenrollment__is_active=1,
                courseenrollment__mode='honor'
            ).order_by('username').values('id', 'username', 'email')
        
        # Cross-reference students with their SSO individual IDs
        user_id_list = enrolled_students.values_list('id', flat=True)
        user_indiv_id_list = get_user_id_with_indiv_id_list(user_id_list)
        if user_indiv_id_list != []:
            user_indiv_id_dict = {user_id: indiv_id for user_id, indiv_id in user_indiv_id_list}
            for user in enrolled_students:
                indiv_id = user_indiv_id_dict.get(user['id'], '')
                user['indiv_id'] = indiv_id
                
        # Fetch or generate course structure and layout
        store = modulestore()
        data_content = cache.get("eol_completion-fast-" + task_input["course_id"] + "-content")
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
            info = data_content[2]
            content = data_content[0]
            max_unit = data_content[1]
            
        # Calculate completion ticks using fast vectorized operations
        data = EolCompletionDataFast().get_ticks_fast(
            content, info, enrolled_students, course_key, max_unit)

    times = datetime.now()
    times = times.strftime("%d/%m/%Y, %H:%M:%S")
    data['time'] = times
    data['is_bigcourse'] = is_bigcourse
    data['time_queue'] = str(TIME_CACHE / 60)
    current_step = {'step': 'Uploading Data Eol Completion Fast'}
    
    # Compress matrix using zlib to bypass Memcached 1MB limits. For large courses that's not enough, memcached limits needs to be modified.
    try:
        payload = zlib.compress(json.dumps(data).encode('utf-8'))
    except Exception as e:
        logger.error(f"EolCompletionFast compress error: {e}")
        payload = data

    cache.set(
        "eol_completion-fast-" +
        task_input["course_id"] +
        "-data",
        payload,
        TIME_CACHE)

    return task_progress.update_task_state(extra_meta=current_step)

def task_process_tick_fast(request, course_id, display_name_course, is_bigcourse):
    course_key = CourseKey.from_string(course_id)
    task_type = 'EOL_Completion_Fast'
    task_class = process_tick_fast
    task_input = {'course_id': course_id, 'display_name': display_name_course, 'is_bigcourse': is_bigcourse}
    task_key = course_id

    return submit_task(
        request,
        task_type,
        task_class,
        course_key,
        task_input,
        task_key)

class EolCompletionFragmentFastView(EolCompletionFragmentView):

    def get_context(self, request, course_id, course, course_key):
        data = cache.get("eol_completion-fast-" + course_id + "-content")
        if data is None:
            store = modulestore()
            info = self.dump_module(store.get_course(course_key))

            id_course = str(BlockUsageLocator(course_key, "course", "course"))
            if 'i4x://' in id_course:
                id_course = str(
                    BlockUsageLocator(
                        course_key,
                        "course",
                        course.display_name))
            data = []
            content, maxn = self.get_content(info, id_course)

            data.extend([content])
            data.extend([maxn])
            data.extend([info])
            cache.set("eol_completion-fast-" + course_id + "-content", data, TIME_CACHE)

        context = {
            "course": course,
            'page_url': reverse(
                'completion_fast_view',
                kwargs={
                    'course_id': six.text_type(course_key)}),
            'data_url': '{}?is_bigcourse=0'.format(reverse(
                'completion_data_fast_view',
                kwargs={
                    'course_id': six.text_type(course_key)})),
            "content": data[0],
            "max_unit": data[1]}

        return context

    def get_context_big_course(self, course, course_key):
        context = {
            "course": course,
            'page_url': reverse(
                'completion_fast_view',
                kwargs={
                    'course_id': six.text_type(course_key)}),
            'data_url': '{}?is_bigcourse=1'.format(reverse(
                'completion_data_fast_view',
                kwargs={
                    'course_id': six.text_type(course_key)})),
            }
        return context

class EolCompletionDataFast(EolCompletionData):
    def get_context(self, request, course_id, display_name_course, is_bigcourse):
        """
            Return eol completion data
        """
        cached_data = cache.get("eol_completion-fast-" + course_id + "-data")
        data = None
        # Attempt to retrieve and decompress cached matrix data
        if cached_data is not None:
            if isinstance(cached_data, bytes):
                try:
                    data = json.loads(zlib.decompress(cached_data).decode('utf-8'))
                except Exception as e:
                    logger.error(f"EolCompletionFast decompress error: {e}")
            else:
                data = cached_data
                
        # If cache miss, kick off an asynchronous Celery task to generate it
        if data is None:
            data = {"data": [[False]]}
            try:
                task_process_tick_fast(request, course_id, display_name_course, is_bigcourse)
            except AlreadyRunningError:
                pass
        context = data
        return context

    # get_context_big_course is inherited identically from EolCompletionData
    def get_1d_completion(info, enrolled_students, course_key):
        # esta función también está en get_ticks_fast, habría que moverla a utils.
        def get_units(info, not_completable_blocks=('discussion+block', 'eoldiscussion+block')):
            course_root = next((v for v in info.values() if v.get('category') == 'course'), None)
            if not course_root:
                return [], []
            unit_reqs = []
            section_sizes = []
            for section_id in course_root.get('children', []):
                section = info.get(section_id, {})
                curr_sec = 0
                for subsection_id in section.get('children', []):
                    subsection = info.get(subsection_id, {})
                    for unit_id in subsection.get('children', []):
                        unit = info.get(unit_id, {})
                        children = unit.get('children', [])
                        if children:
                            # se descarta el `discussion+block` al igual que en https://github.com/eol-uchile/eol_completion/blob/8d90f07eb0f2ffa639007f9893c8876fd6f8441f/eol_completion/views.py#L487
                            unit_reqs.append(tuple(UsageKey.from_string(b) for b in children if all(x not in b for x in not_completable_blocks)))
                            
                            curr_sec += 1
                if curr_sec > 0:
                    section_sizes.append(curr_sec)
            return unit_reqs, section_sizes
        
        unit_reqs, _ = get_units(info) # ver como se obtiene info y enrolled_students en task_get_tick_fast L#591
        context_key = LearningContextKey.from_string(str(course_key))
        students_id = map(list, zip(*((x['id']) for x in enrolled_students))) 
        user_idx_map = {uid: i for i, uid in enumerate(students_id)}  
        unique_blocks = [item for sublist in unit_reqs for item in sublist] # All course completable blocks in the course, concatenated by unit
        block_idx_map = {b: k for k, b in enumerate(unique_blocks)}
        N = len(students_id)    # Number of students
        B = len(unique_blocks)  # Number of completable blocks in the course
        C_1d = np.zeros(N, dtype=np.uint8)
        qs2 = BlockCompletion.objects.filter(user_id__in=students_id, context_key=context_key, completion=1.0, block_key__in=block_idx_map).exclude(Q(block_key__icontains='discussion') | Q(block_key__icontains='eoldiscussion')).values('user_id').annotate(block_count=Count('block_key'))
        for row in qs2.iterator(chunk_size=100000):
            uid = row['user_id']
            if uid in user_idx_map:
                C_1d[user_idx_map[uid]] = row['block_count']
        return C_1d
        # Luego la info para el endpoint es C_1d/B
        
    def get_ticks_fast(self, content, info, enrolled_students, course_key, max_unit):
        # do not process the course in case you have 0 students enrolled
        if not enrolled_students:
            return {'data': [[True]], 'completion': []}
        
        students_id, students_username, students_email, students_indiv_id = map(list, zip(*((x['id'], x['username'], x['email'], x['indiv_id']) for x in enrolled_students)))

        def get_certificates(students_id, course_key):
            certificates = GeneratedCertificate.objects.filter(status='downloadable', user_id__in=students_id, course_id=course_key).values_list("user_id", flat=True)
            cert_flags = np.isin(students_id, list(set(certificates))).view(np.uint8)
            cert_strs  = np.where(cert_flags, 'Si', 'No')
            return cert_strs, cert_flags
        
        cert_strs, cert_flags = get_certificates(students_id, course_key)

        # Parse `info` (info = Content().dump_module(store.get_course(course_key))) to identify required blocks per unit and section layout
        # Hierarchy: course -> chapter(section) -> sequential(subsection) -> vertical(unit)

        def get_units(info, not_completable_blocks=('discussion+block', 'eoldiscussion+block')):
            course_root = next((v for v in info.values() if v.get('category') == 'course'), None)
            if not course_root:
                return [], []
            unit_reqs = []
            section_sizes = []
            for section_id in course_root.get('children', []):
                section = info.get(section_id, {})
                curr_sec = 0
                for subsection_id in section.get('children', []):
                    subsection = info.get(subsection_id, {})
                    for unit_id in subsection.get('children', []):
                        unit = info.get(unit_id, {})
                        children = unit.get('children', [])
                        if children:
                            # se descarta el `discussion+block` al igual que en https://github.com/eol-uchile/eol_completion/blob/8d90f07eb0f2ffa639007f9893c8876fd6f8441f/eol_completion/views.py#L487
                            unit_reqs.append(tuple(UsageKey.from_string(b) for b in children if all(x not in b for x in not_completable_blocks)))
                            
                            curr_sec += 1
                if curr_sec > 0:
                    section_sizes.append(curr_sec)
            return unit_reqs, section_sizes
        
        unit_reqs, section_sizes = get_units(info)

        # Extract unique blocks to form the column space of the completion matrix
        # unit_reqs (list of required block tuples)
        # unique_blocks (list of unique UsageKey blocks across the course)

        unique_blocks = [item for sublist in unit_reqs for item in sublist] # All course completable blocks in the course, concatenated by unit
        
        N = len(students_id)    # Number of students
        B = len(unique_blocks)  # Number of completable blocks in the course
        U = len(unit_reqs)      # Number of units in the course (verticals)

        if B > 0:
            # Map user IDs and block keys to indices for O(1) matrix insertion
            user_idx_map = {uid: i for i, uid in enumerate(students_id)}
            user_ids   = [int(uid) for uid in students_id]

            block_idx_map = {b: k for k, b in enumerate(unique_blocks)}
            block_keys = [str(k) for k in block_idx_map.keys()]
            block_idx_map_keys = {b: k for k, b in enumerate(block_keys)} # It gives an additional numerical order to the blocks
            
            # Fetch all matching block completions and populate the C matrix
            context_key = LearningContextKey.from_string(str(course_key))
            
            # NOTA: los bloques en qs no necesitan venir ordenados por estructura y tampoco lo hacen los bloques de la estrcutura. Las unidades por otro lado si y como estas estarán relacionados con los bloques, les darán el orden a nivel matricial.
            # qs = BlockCompletion.objects.filter(user_id__in=students_id, context_key=context_key, completion=1.0, block_key__in=block_idx_map).values('user_id', 'block_key')
            
            def chunked(iterable, size):
                it = iter(iterable)
                while chunk := list(islice(it, size)):
                    yield chunk
            
            def build_completion_matrix(user_ids, block_keys, user_idx_map, block_idx_map_keys, context_key, chunk_size=1000):
                """
                Builds the N x B completion matrix by streaming DB results directly into
                the matrix - never accumulating all rows in memory at once.
                """
                COMPLETION_TABLE_NAME = 'completion_blockcompletion'
                N = len(user_ids)
                B = len(block_keys)
                C = np.zeros((N, B), dtype=np.uint8)
                block_placeholders = ','.join(['%s'] * len(block_keys))
                for user_chunk in chunked(user_ids, chunk_size):
                    user_placeholders = ','.join(['%s'] * len(user_chunk))
                    query = f"""
                        SELECT user_id, block_key FROM {COMPLETION_TABLE_NAME} WHERE course_key = %s AND completion = 1.0 
                        AND user_id IN ({user_placeholders}) AND block_key IN ({block_placeholders})
                    """
                    params = [str(context_key)] + user_chunk + block_keys

                    with connection.cursor() as cursor:
                        cursor.execute(query, params)
                        # write directly into C
                        row_idx, col_idx = [], []
                        # iterate the cursor
                        for uid, bk in cursor:
                            if uid in user_idx_map and bk in block_idx_map_keys:
                                row_idx.append(user_idx_map[uid])
                                col_idx.append(block_idx_map_keys[bk])
                        if row_idx:
                            C[row_idx, col_idx] = 1
                return C
                
            C = build_completion_matrix(user_ids, block_keys, user_idx_map, block_idx_map_keys, context_key, chunk_size=1000)

            # Construct a structural matrix M (B blocks x U units)
            M = np.zeros((B, U), dtype=np.uint8)
            if unit_reqs:
                pairs = np.array([(block_idx_map[b], j) for j, req in enumerate(unit_reqs) for b in req], dtype=np.intp,)
                if pairs.size:
                    M[pairs[:, 0], pairs[:, 1]] = 1

            # Compute Unit completions by matrix dot product C * M
            # If student completed all required blocks for a unit, the product equals the block count
            # UC (N x U) =  C (N x B) * M (B x U) == M.sum (U)
            unit_completions = (C @ M == M.sum(axis=0)).astype(np.bool_)

        else:
            # If no blocks are required, assume all units are implicitly completed
            unit_completions = np.ones((N, U), dtype=np.bool_)
        
        TICK = '&#10004;'
        cols = []
        completion_acc = []
        
        # Aggregate unit completions into section fractions (e.g. '3/4')
        start_u = 0
        for size in section_sizes:
            if size <= 0:
                continue
            end_u = start_u + size
            sec_units = unit_completions[:, start_u:end_u]
            unit_strs = np.where(sec_units == 1, TICK, '')
            for j in range(size):
                cols.append(unit_strs[:, j])
                completion_acc.append(np.sum(sec_units[:, j]))
            sec_sum = np.sum(sec_units, axis=1)
            cols.append(np.array([f"{v}/{size}" for v in sec_sum], dtype=object))
            sec_done_flags = (sec_sum == size).astype(int)
            completion_acc.append(np.sum(sec_done_flags))
            start_u = end_u
            
        # Aggregate global total fraction for the entire course (e.g. '12/12')
        total_sum = np.sum(unit_completions, axis=1)
        total_strs = np.char.add(total_sum.astype(str), f"/{max_unit}")
        cols.append(total_strs)
        
        if max_unit > 0:
            total_done_flags = (total_sum == max_unit).astype(int)
        else:
            total_done_flags = np.zeros(N, dtype=int)
            
        completion_acc.append(np.sum(total_done_flags))        
        completion_acc.append(np.sum(cert_flags))
        
        # Horizontally stack student metadata with computed layout columns into a final payload
        indiv_ids = np.array([x if x is not None else '' for x in students_indiv_id], dtype=object)
        usernames = np.array(students_username, dtype=object)
        emails = np.array(students_email, dtype=object)
        
        data_matrix = np.column_stack([emails, usernames, indiv_ids] + cols + [cert_strs])
        user_tick = {
            'data': data_matrix.tolist(),
            'completion': [str(x) for x in completion_acc]
        }
        return user_tick
