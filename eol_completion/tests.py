# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from openedx.core.lib.tests.tools import assert_true
from mock import patch, Mock


from django.test import TestCase, Client
from django.test.client import RequestFactory
from django.urls import reverse
from django.contrib.auth.models import User
from util.testing import UrlResetMixin
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory
from student.tests.factories import UserFactory, CourseEnrollmentFactory
from capa.tests.response_xml_factory import StringResponseXMLFactory
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory
from completion import models
from opaque_keys.edx.keys import CourseKey
from courseware.courses import get_course_with_access
from lms.djangoapps.certificates.models import GeneratedCertificate
from six import text_type
from six.moves import range
import json
import views

USER_COUNT = 11


class TestEolCompletionView(UrlResetMixin, ModuleStoreTestCase):
    def setUp(self):
        super(TestEolCompletionView, self).setUp()
        # create a course
        self.course = CourseFactory.create(org='mss', course='999',
                                           display_name='eol_completion_course')

        # Now give it some content
        with self.store.bulk_operations(self.course.id, emit_signals=False):
            chapter = ItemFactory.create(
                parent_location=self.course.location,
                category="chapter",
            )
            section = ItemFactory.create(
                parent_location=chapter.location,
                category="sequential",
            )
            subsection = ItemFactory.create(
                parent_location=section.location,
                category="vertical",
            )
            self.items = [
                ItemFactory.create(
                    parent_location=subsection.location,
                    category="problem"
                )
                for __ in range(USER_COUNT - 1)
            ]

        # Create users, enroll
        self.users = [UserFactory.create() for _ in range(USER_COUNT)]
        for user in self.users:
            CourseEnrollmentFactory(user=user, course_id=self.course.id)

        # Patch the comment client user save method so it does not try
        # to create a new cc user when creating a django user
        with patch('student.models.cc.User.save'):
            # Create the student
            self.student = UserFactory(username='student', password='test', email='student@edx.org')
            # Enroll the student in the course
            CourseEnrollmentFactory(user=self.student, course_id=self.course.id)

            # Create and Enroll staff user
            self.staff_user = UserFactory(username='staff_user', password='test', email='staff@edx.org', is_staff=True)
            CourseEnrollmentFactory(user=self.staff_user, course_id=self.course.id)

            # Log the student in
            self.client = Client()
            assert_true(self.client.login(username='student', password='test'))

            # Log the user staff in
            self.staff_client = Client()
            assert_true(self.staff_client.login(username='staff_user', password='test'))

    def test_render_page(self):
        url = reverse('completion_view', kwargs={'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)

    def test_render_data(self):
        url = reverse('completion_data_view', kwargs={'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content)
        self.assertEqual(len(data['data']), 13)
        self.assertEqual(data['data'][-1], [u'student@edx.org', u'student', u'', u'0/1', u'0/1', u'No'])

    def test_render_data_wrong_course(self):
        url = reverse('completion_data_view', kwargs={'course_id': 'course-v1:mss+MSS001+2019_2'})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_page_wrong_course(self):
        url = reverse('completion_view', kwargs={'course_id': 'course-v1:mss+MSS001+2019_2'})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_page_no_staff(self):
        url = reverse('completion_view', kwargs={'course_id': self.course.id})
        self.response = self.client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_data_no_staff(self):
        url = reverse('completion_data_view', kwargs={'course_id': self.course.id})
        self.response = self.client.get(url)
        self.assertEqual(self.response.status_code, 302)

    def test_render_blockcompletion(self):
        for item in self.items:
            usage_key = item.scope_ids.usage_id
            completion = models.BlockCompletion.objects.create(
                user=self.student,
                course_key=self.course.id,
                block_key=usage_key,
                completion=1.0,
            )

        url = reverse('completion_data_view', kwargs={'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content)
        self.assertEqual(len(data['data']), 13)
        self.assertEqual(data['data'][-1], [u'student@edx.org', u'student', u'&#10004;', u'1/1', u'1/1', u'No'])

    def test_render_certificate(self):
        GeneratedCertificate.objects.create(user=self.student, course_id=self.course.id)

        url = reverse('completion_data_view', kwargs={'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content)
        self.assertEqual(len(data['data']), 13)
        self.assertEqual(data['data'][-1], [u'student@edx.org', u'student', u'', u'0/1', u'0/1', u'Si'])
