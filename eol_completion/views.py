# -*- coding: utf-8 -*-



import six
import json
import math
from django.conf import settings
from datetime import datetime
from courseware.courses import get_course_with_access
from django.template.loader import render_to_string
from web_fragments.fragment import Fragment
from django.core.cache import cache
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from lms.djangoapps.certificates.models import GeneratedCertificate
from xblock.fields import Scope
from opaque_keys.edx.keys import CourseKey, UsageKey, LearningContextKey
from opaque_keys import InvalidKeyError
from django.contrib.auth.models import User
from django.urls import reverse
from xblock_discussion import DiscussionXBlock
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.inheritance import compute_inherited_metadata, own_metadata
from courseware.access import has_access, get_user_role
from completion.models import BlockCompletion
from collections import OrderedDict, defaultdict, deque
from django.http import HttpResponse, Http404, HttpResponseServerError, JsonResponse
from django.views.generic.base import View
from opaque_keys.edx.locator import CourseLocator, BlockUsageLocator
from celery import current_task, task
from lms.djangoapps.instructor_task.tasks_base import BaseInstructorTask
from lms.djangoapps.instructor_task.api_helper import submit_task
from functools import partial
from time import time
from lms.djangoapps.instructor_task.tasks_helper.runner import run_main_task, TaskProgress
from django.db import IntegrityError, transaction
from django.utils.translation import ugettext_noop
from pytz import UTC
from lms.djangoapps.instructor_task.api_helper import AlreadyRunningError
from numpy import sum
from django.core.exceptions import FieldError
import logging
# Create your views here.

logger = logging.getLogger(__name__)
FILTER_LIST = ['xml_attributes']
INHERITED_FILTER_LIST = ['children', 'xml_attributes']


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
    display_name_course = task_input["display_name"]
    try:
        enrolled_students = User.objects.filter(
            courseenrollment__course_id=course_key,
            courseenrollment__is_active=1
        ).order_by('username').values('id', 'username', 'email', 'edxloginuser__run')
    except FieldError:
        enrolled_students = User.objects.filter(
            courseenrollment__course_id=course_key,
            courseenrollment__is_active=1
        ).order_by('username').values('id', 'username', 'email')
    start_time = time()
    start_date = datetime.now(UTC)
    task_progress = TaskProgress(
        action_name,
        enrolled_students.count(),
        start_time)

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
    data['time_queue'] = str(settings.EOL_COMPLETION_TIME_CACHE / 60)
    current_step = {'step': 'Uploading Data Eol Completion'}
    cache.set(
        "eol_completion-" +
        task_input["course_id"] +
        "-data",
        data,
        settings.EOL_COMPLETION_TIME_CACHE)

    return task_progress.update_task_state(extra_meta=current_step)


def task_process_tick(request, course_id, display_name_course):
    course_key = CourseKey.from_string(course_id)
    task_type = 'EOL_Completion'
    task_class = process_tick
    task_input = {'course_id': course_id, 'display_name': display_name_course}
    task_key = ""

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
        if not staff_access:
            raise Http404()
        context = self.get_context(request, course_id, course, course_key)

        html = render_to_string(
            'eol_completion/eol_completion_fragment.html', context)
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
            cache.set("eol_completion-" + course_id + "-content", data, settings.EOL_COMPLETION_TIME_CACHE)

        context = {
            "course": course,
            'page_url': reverse(
                'completion_view',
                kwargs={
                    'course_id': six.text_type(course_key)}),
            'data_url': reverse(
                'completion_data_view',
                kwargs={
                    'course_id': six.text_type(course_key)}),
            "content": data[0],
            "max_unit": data[1]}

        return context


class EolCompletionData(View, Content):
    @transaction.non_atomic_requests
    def dispatch(self, args, **kwargs):
        return super(EolCompletionData, self).dispatch(args, **kwargs)

    def get(self, request, course_id, **kwargs):
        course_key = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, "load", course_key)
        display_name_course = course.display_name
        staff_access = bool(has_access(request.user, 'staff', course))
        if not staff_access:
            raise Http404()

        context = self.get_context(request, course_id, display_name_course)

        return JsonResponse(context)

    def get_context(self, request, course_id, display_name_course):
        """
            Return eol completion data
        """
        data = cache.get("eol_completion-" + course_id + "-data")
        if data is None:
            data = {"data": [[False]]}
            try:
                task_process_tick(request, course_id, display_name_course)
            except AlreadyRunningError:
                pass
        context = data

        return context

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
        students_rut = [x['edxloginuser__run'] for x in enrolled_students if 'edxloginuser__run' in x]
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
            if len(students_rut) > 0:
                aux_user_tick.appendleft(students_rut[i - 1] if students_rut[i - 1] != None else '')
            else:
                aux_user_tick.appendleft('')
            aux_user_tick.appendleft(students_username[i - 1])
            aux_user_tick.appendleft(students_email[i - 1])
            aux_user_tick.append('Si' if user in certificate else 'No')
            user_tick['data'].append(list(aux_user_tick))
            if len(completion) != 0:
                completion = sum([completion, aux_completion], 0)
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
