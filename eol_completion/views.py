# -*- coding: utf-8 -*-

# Python Standard Libraries
import json
import logging
import six
import zlib

# Installed packages (via pip)
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.generic.base import View

# Edx dependencies
from common.djangoapps.student.models import CourseAccessRole
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor_task.api_helper import AlreadyRunningError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from web_fragments.fragment import Fragment
from xmodule.modulestore.django import modulestore

# Internal project dependencies
from .exceptions import CompressionException
from .tasks import task_process_tick
from .utils import Content

logger = logging.getLogger(__name__)
FILTER_LIST = ['xml_attributes']
INHERITED_FILTER_LIST = ['children', 'xml_attributes']
LIMIT_STUDENTS = 10000
TIME_CACHE  = 300
if hasattr(settings, 'EOL_COMPLETION_TIME_CACHE'):
    TIME_CACHE = settings.EOL_COMPLETION_TIME_CACHE 

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
        staff_query = CourseAccessRole.objects.filter(
            course_id=course_key
        ).values('user_id')
        student_count = User.objects.filter(
            courseenrollment__course_id=course_key,
            courseenrollment__is_active=1
        ).exclude(
            id__in=staff_query
        ).count()
        is_big = limit_student < student_count
        if not is_big:
            context = self.get_context( course_id, course, course_key)
            html = render_to_string(
            'eol_completion/eol_completion_fragment.html', context)
        else:
            context = self.get_context_big_course_url(course, course_key)
            html = render_to_string(
                'eol_completion/eol_completion_bigcourse.html', context)
        fragment = Fragment(html)
        return fragment

    def get_context(self, course_id, course, course_key):
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

    def get_context_big_course_url(self, course, course_key):
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

        context = self.get_context(request, course_id, display_name_course, is_bigcourse)

        return JsonResponse(context)

    def get_context(self, request, course_id, display_name_course, is_bigcourse):
        """
            Return eol completion data
        """
        data = cache.get("eol_completion-" + course_id + "-data")
        if data is not None:
            if isinstance(data, bytes):
                try:
                    data = json.loads(zlib.decompress(data).decode('utf-8'))
                except Exception as e:
                    logger.error(f"EolCompletion decompress error: {e}")
                    raise CompressionException(f"Failed to decompress cached data for course_id={course_id}") from e

        if data is None:
            data = {"data": [[False]]}
            try:
                task_process_tick(request, course_id, display_name_course, is_bigcourse)
            except AlreadyRunningError:
                pass
        context = data

        return context
