# -*- coding: utf-8 -*-

from mock import patch, Mock


from django.test import TestCase, Client
from django.test.client import RequestFactory
from django.urls import reverse
from django.contrib.auth.models import User
from util.testing import UrlResetMixin
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory
from student.roles import CourseStaffRole
from student.tests.factories import UserFactory, CourseEnrollmentFactory
from capa.tests.response_xml_factory import StringResponseXMLFactory
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory
from completion import models
from opaque_keys.edx.keys import CourseKey, LearningContextKey
from courseware.courses import get_course_with_access
from lms.djangoapps.certificates.models import GeneratedCertificate
from common.djangoapps.student.tests.factories import CourseAccessRoleFactory
from six import text_type
from six.moves import range
import json
from . import views
import time
USER_COUNT = 11


class TestEolCompletionView(UrlResetMixin, ModuleStoreTestCase):
    def setUp(self):
        super(TestEolCompletionView, self).setUp()
        # create a course
        self.course = CourseFactory.create(
            org='mss', course='999', display_name='eol_completion_course')

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
            CourseEnrollmentFactory(user=user, course_id=self.course.id, mode='honor')
        # create a course without content, only student
        self.course_no_content = CourseFactory.create(
            org='mnc', course='777', display_name='eol_completion_course2')
        for user in self.users:
            CourseEnrollmentFactory(user=user, course_id=self.course_no_content.id, mode='honor')
        # create a course without student, only content
        self.course_no_user = CourseFactory.create(
            org='mnu', course='888', display_name='eol_completion_course3')
        # Now give it some content
        with self.store.bulk_operations(self.course_no_user.id, emit_signals=False):
            chapter2 = ItemFactory.create(
                parent_location=self.course_no_user.location,
                category="chapter",
            )
            section2 = ItemFactory.create(
                parent_location=chapter2.location,
                category="sequential",
            )
            subsection2 = ItemFactory.create(
                parent_location=section2.location,
                category="vertical",
            )
            self.items2 = [
                ItemFactory.create(
                    parent_location=subsection2.location,
                    category="problem"
                )
                for __ in range(USER_COUNT - 1)
            ]
        # create a empty course
        self.course_empty = CourseFactory.create(
            org='mem', course='111', display_name='eol_completion_course4')
        # Patch the comment client user save method so it does not try
        # to create a new cc user when creating a django user
        with patch('student.models.cc.User.save'):
            # Create the student
            self.student = UserFactory(
                username='student',
                password='test',
                email='student@edx.org')
            # Enroll the student in the course
            CourseEnrollmentFactory(
                user=self.student, course_id=self.course.id, mode='honor')
            CourseEnrollmentFactory(
                user=self.student, course_id=self.course_no_content.id, mode='honor')

            # Create and Enroll staff user
            self.staff_user = UserFactory(
                username='staff_user',
                password='test',
                email='staff@edx.org')
            CourseEnrollmentFactory(
                user=self.staff_user,
                course_id=self.course.id, mode='audit')
            CourseStaffRole(self.course.id).add_users(self.staff_user)

            # Create and Enroll data researcher user
            self.data_researcher_user = UserFactory(
                username='data_researcher_user',
                password='test',
                email='data.researcher@edx.org')
            CourseEnrollmentFactory(
                user=self.data_researcher_user,
                course_id=self.course.id, mode='audit')
            CourseAccessRoleFactory(
                course_id=self.course.id,
                user=self.data_researcher_user,
                role='data_researcher',
                org=self.course.id.org
            )
            self.client_data_researcher = Client()
            self.assertTrue(self.client_data_researcher.login(username='data_researcher_user', password='test'))
            # Log the student in
            self.client = Client()
            self.assertTrue(self.client.login(username='student', password='test'))
            # Create Super User
            self.super_user = UserFactory(
                username='super_user',
                password='test',
                email='super@edx.org',
                is_staff=True)
            self.super_client = Client()
            self.assertTrue(
                self.super_client.login(
                    username='super_user',
                    password='test'))
            # Log the user staff in
            self.staff_client = Client()
            self.assertTrue(
                self.staff_client.login(
                    username='staff_user',
                    password='test'))

    def test_render_page(self):
        """
            Test reder page normal process
        """
        url = reverse('completion_view', kwargs={'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
    
    def test_render_page_data_researcher_user(self):
        """
            Test reder page normal process with data_researcher_user
        """
        url = reverse('completion_view', kwargs={'course_id': self.course.id})
        self.response = self.client_data_researcher.get(url)
        self.assertEqual(self.response.status_code, 200)

    def test_render_data_researcher_user(self):
        """
            Test get data normal process with data_researcher_user
        """
        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course.id})
        self.response = self.client_data_researcher.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.client_data_researcher.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '', '0/1', '0/1', 'No'])

    def test_render_data(self):
        """
            Test get data normal process
        """
        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 13)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '', '0/1', '0/1', 'No'])

    def test_render_data_with_rut(self):
        """
            Test get data normal process with edxloginuser
        """
        try:
            from unittest.case import SkipTest
            from uchileedxlogin.models import EdxLoginUser
        except ImportError:
            self.skipTest("import error uchileedxlogin")
        EdxLoginUser.objects.create(user=self.student, run='000000001K')
        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 13)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '000000001K', '', '0/1', '0/1', 'No'])

    def test_render_data_wrong_course(self):
        """
            Test get data wrong course
        """
        url = reverse(
            'completion_data_view', kwargs={
                'course_id': 'course-v1:mss+MSS001+2019_2'})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_page_wrong_course(self):
        """
            Test render page wrong course
        """
        url = reverse(
            'completion_view', kwargs={
                'course_id': 'course-v1:mss+MSS001+2019_2'})
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_page_no_staff(self):
        """
            Test render page when user is not staff
        """
        url = reverse('completion_view', kwargs={'course_id': self.course.id})
        self.response = self.client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_data_no_staff(self):
        """
            Test get data when user is not staff
        """
        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course.id})
        self.response = self.client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_blockcompletion(self):
        """
            Test get data with block completion
        """
        context_key = LearningContextKey.from_string(str(self.course.id))
        for item in self.items:
            usage_key = item.scope_ids.usage_id
            completion = models.BlockCompletion.objects.create(
                user=self.student,
                context_key=context_key,
                block_key=usage_key,
                completion=1.0,
            )

        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 13)
        self.assertEqual(data['data'][-1],
                         ['student@edx.org',
                          'student',
                          '',
                          '&#10004;',
                          '1/1',
                          '1/1',
                          'No'])

    def test_render_certificate(self):
        """
            Test get data with certificate
        """
        GeneratedCertificate.objects.create(
            user=self.student, course_id=self.course.id, status=u'downloadable')

        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 13)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '', '0/1', '0/1', 'Si'])

    def test_render_certificate_unavailable(self):
        """
            Test get data with unavailable certificate
        """
        GeneratedCertificate.objects.create(
            user=self.student, course_id=self.course.id, status=u'unavailable')

        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course.id})
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 13)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '', '0/1', '0/1', 'No'])

    def test_render_data_no_content(self):
        """
            Test get data without content
        """        
        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course_no_content.id})
        self.response = self.super_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.super_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(data['completion'], ["0", "0", "0"])
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '0/0', '0/0', 'No'])
    
    def test_render_data_no_users(self):
        """
            Test get data without users
        """        
        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course_no_user.id})
        self.response = self.super_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.super_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'], [[True]])

    def test_render_data_course_empty(self):
        """
            Test get data with empty course
        """        
        url = reverse(
            'completion_data_view', kwargs={
                'course_id': self.course_empty.id})
        self.response = self.super_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.super_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'], [[True]])
        self.assertEqual(data['completion'], [])