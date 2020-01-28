# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import six
import json
import math
from datetime import datetime
from courseware.courses import get_course_with_access
from django.template.loader import render_to_string
from django.shortcuts import render_to_response
from web_fragments.fragment import Fragment
from django.core.cache import cache
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from lms.djangoapps.certificates.models import GeneratedCertificate
from xblock.fields import Scope
from opaque_keys.edx.keys import CourseKey, UsageKey
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
# Create your views here.

FILTER_LIST = ['xml_attributes']
INHERITED_FILTER_LIST = ['children', 'xml_attributes']


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
                module) for field in module.fields.values() if is_inherited(field)}
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

        data = cache.get("eol_completion-" + course_id + "-content")
        if data is None:
            store = modulestore()
            # Dictionary with all course blocks
            info = self.dump_module(store.get_course(course_key))

            id_course = str(BlockUsageLocator(course_key, "course", "course"))
            if 'i4x://' in id_course:
                id_course = str(BlockUsageLocator(course_key, "course", course.display_name))
            data = []
            content, maxn = self.get_content(info, id_course)

            time = datetime.now()
            time = time.strftime("%d/%m/%Y, %H:%M:%S")
            data.extend([content, time])
            cache.set("eol_completion-" + course_id + "-content", data, 300)

        context = {
            "course": course,
            'page_url': reverse(
                'completion_view',
                kwargs={
                    'course_id': six.text_type(course_key)}),
            "content": data[0],
            "time": data[1]}

        return context


class EolCompletionData(View, Content):
    def get(self, request, course_id, **kwargs):
        course_key = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, "load", course_key)
        context = self.get_context(request, course_id, course, course_key)

        return JsonResponse(context)

    def get_context(self, request, course_id, course, course_key):
        data = cache.get("eol_completion-" + course_id + "-data")
        if data is None:
            enrolled_students = User.objects.filter(
                courseenrollment__course_id=course_key,
                courseenrollment__is_active=1
            ).order_by('username').values('id', 'username', 'email')

            store = modulestore()
            # Dictionary with all course blocks
            info = self.dump_module(store.get_course(course_key))

            id_course = str(BlockUsageLocator(course_key, "course", "course"))
            if 'i4x://' in id_course:
                id_course = str(BlockUsageLocator(course_key, "course", course.display_name))
            data = []
            content, max_unit = self.get_content(info, id_course)
            user_tick = self.get_ticks(
                content, info, enrolled_students, course_key, max_unit)

            data.extend([user_tick])
            cache.set("eol_completion-" + course_id + "-data", data, 300)

        context = data[0]

        return context

    def get_ticks(
            self,
            content,
            info,
            enrolled_students,
            course_key,
            max_unit):
        """
            Dictionary of students with true/false if students completed the units
        """
        user_tick = defaultdict(list)

        students_id = [x['id'] for x in enrolled_students]
        students_username = [x['username'] for x in enrolled_students]
        students_email = [x['email'] for x in enrolled_students]
        i = 0
        certificate = self.get_certificate(students_id, course_key)
        blocks = self.get_block(students_id, course_key)

        for user in students_id:
            i += 1
            # Get a list of true/false if they completed the units
            # and number of completed units
            data = self.get_data_tick(content, info, user, blocks, max_unit)
            aux_user_tick = deque(data)
            aux_user_tick.appendleft(students_username[i - 1])
            aux_user_tick.appendleft(students_email[i - 1])
            aux_user_tick.append('Si' if user in certificate else 'No')
            user_tick['data'].append(list(aux_user_tick))
        return user_tick

    def get_block(self, students_id, course_key):
        """
            Get all completed students block
        """
        aux_blocks = BlockCompletion.objects.filter(
            user_id__in=students_id,
            course_key=course_key,
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
        completed_unit = 0  # Number of completed units per student
        completed_unit_per_section = 0  # Number of completed units per section
        num_units_section = 0  # Number of units per section
        first = True
        for unit in content.items():
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
                else:
                    data.append('&#10004;')
            if not first and unit[1]['type'] == 'section' and unit[1]['num_children'] > 0:
                aux_point = str(completed_unit_per_section) + \
                    "/" + str(num_units_section)
                data.append(aux_point)
                completed_unit_per_section = 0
                num_units_section = 0
            if first and unit[1]['type'] == 'section' and unit[1]['num_children'] > 0:
                first = False
        aux_point = str(completed_unit_per_section) + \
            "/" + str(num_units_section)
        data.append(aux_point)
        aux_final_point = str(completed_unit) + "/" + str(max_unit)
        data.append(aux_final_point)
        return data

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
        certificates = GeneratedCertificate.objects.filter(
            user_id__in=students_id, course_id=course_id).values("user_id")
        cer_students_id = [x["user_id"] for x in certificates]

        return cer_students_id
