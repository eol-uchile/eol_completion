# -*- coding: utf-8 -*-

# Python Standard Libraries
import json

# Installed packages (via pip)
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from mock import patch
from six.moves import range

# Edx dependencies
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory, CourseEnrollmentFactory, CourseAccessRoleFactory
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.certificates.models import GeneratedCertificate
from opaque_keys.edx.keys import LearningContextKey
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory

# Internal project dependencies
from completion import models

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
        with patch('common.djangoapps.student.models.cc.User.save'):
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

    def get_completion_url(self, course_id):
        """
            Returns the url for EolCompletionFragmentView for a given course_id
        """
        return reverse('completion_view', kwargs={'course_id': course_id})

    def get_completion_data_url(self, course_id, is_bigcourse):
        """
            Returns the url for EolCompletionData for a given course_id.
            Depending on the is_bigcourse boolean, this will route to the appropriate completion data endpoint.

        """
        if is_bigcourse is not None:
            return f'{reverse("completion_data_view", kwargs={"course_id": course_id})}?is_bigcourse={is_bigcourse}'
        return reverse('completion_data_view', kwargs={'course_id': course_id})

    def test_render_page(self):
        """
            Test reder page normal process
        """
        url = self.get_completion_url(self.course.id)
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)

    @override_settings(EOL_COMPLETION_LIMIT_STUDENT=2)
    def test_render_page_big_course(self):
        """
            Test reder page normal process when is big course
        """
        url = self.get_completion_url(self.course.id)
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
  
    def test_render_page_data_researcher_user(self):
        """
            Test reder page normal process with data_researcher_user
        """
        url = self.get_completion_url(self.course.id)
        self.response = self.client_data_researcher.get(url)
        self.assertEqual(self.response.status_code, 200)

    def test_render_data_researcher_user(self):
        """
            Test get data normal process with data_researcher_user
        """
        url = self.get_completion_data_url(self.course.id, 0)
        self.response = self.client_data_researcher.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.client_data_researcher.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '', '0/1', '0/1', 'No'])

    def test_render_data_big_course(self):
        """
            Test get data normal process when is big course
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
        url = self.get_completion_data_url(self.course.id, 1)
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(data['data'][-1][0], self.student.username)
        self.assertEqual(data['data'][-1][1], '')
        self.assertEqual(data['data'][-1][2], self.student.email)
        self.assertEqual(data['data'][-1][3], completion.modified.strftime("%d/%m/%Y, %H:%M:%S"))

    def test_render_data(self):
        """
            Test get data normal process
        """
        url = '{}?is_bigcourse=0'.format(reverse(
            'completion_data_view', kwargs={
                'course_id': self.course.id}))
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '', '0/1', '0/1', 'No'])

    @patch('eol_completion.tasks.get_user_id_with_indiv_id_list')
    def test_render_data_with_indiv_id(self, mock_user_id_with_indiv_id_list):
        """
            Test get data normal process with edxloginuser
        """
        mock_user_id_with_indiv_id_list.return_value = [(self.student.id, '000000001K')]
        url = self.get_completion_data_url(self.course.id, 0)
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '000000001K', '', '0/1', '0/1', 'No'])

    @patch('eol_completion.utils.get_user_id_with_indiv_id_list')
    def test_render_data_with_indiv_id_big_course(self, mock_user_id_with_indiv_id_list):
        """
            Test get data normal process with edxloginuser when is big course
        """
        mock_user_id_with_indiv_id_list.return_value = [(self.student.id, '000000001K')]
        context_key = LearningContextKey.from_string(str(self.course.id))
        for item in self.items:
            usage_key = item.scope_ids.usage_id
            completion = models.BlockCompletion.objects.create(
                user=self.student,
                context_key=context_key,
                block_key=usage_key,
                completion=1.0,
            )
        url = self.get_completion_data_url(self.course.id, 1)
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(data['data'][-1][0], self.student.username)
        self.assertEqual(data['data'][-1][1], '000000001K')
        self.assertEqual(data['data'][-1][2], self.student.email)
        self.assertEqual(data['data'][-1][3], completion.modified.strftime("%d/%m/%Y, %H:%M:%S"))

    def test_render_data_wrong_course(self):
        """
            Test get data wrong course
        """
        url = self.get_completion_data_url('course-v1:mss+MSS001+2019_2', 0)
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_page_wrong_course(self):
        """
            Test render page wrong course
        """
        url = self.get_completion_url('course-v1:mss+MSS001+2019_2')
        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_page_no_staff(self):
        """
            Test render page when user is not staff
        """
        url = self.get_completion_url(self.course.id)
        self.response = self.client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_data_no_staff(self):
        """
            Test get data when user is not staff
        """
        url = self.get_completion_data_url(self.course.id, 0)
        self.response = self.client.get(url)
        self.assertEqual(self.response.status_code, 404)

    def test_render_data_is_bigcourse_wrong_params(self):
        """
            Test get data when is_bigcourse is not defined or is wrong
        """
        url = self.get_completion_data_url(self.course.id, None)
        self.response = self.client.get(url)
        self.assertEqual(self.response.status_code, 404)

        url = self.get_completion_data_url(self.course.id, 'asd')
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

        url = self.get_completion_data_url(self.course.id, 0)
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
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

        url = self.get_completion_data_url(self.course.id, 0)
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '', '0/1', '0/1', 'Si'])

    def test_render_certificate_unavailable(self):
        """
            Test get data with unavailable certificate
        """
        GeneratedCertificate.objects.create(
            user=self.student, course_id=self.course.id, status=u'unavailable')

        url = self.get_completion_data_url(self.course.id, 0)
        self.response = self.staff_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.staff_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '', '0/1', '0/1', 'No'])

    def test_render_data_no_content(self):
        """
            Test get data without content
        """
        url = self.get_completion_data_url(self.course_no_content.id, 0)
        self.response = self.super_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.super_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(data['completion'], ["0", "0"])
        self.assertEqual(
            data['data'][-1], ['student@edx.org', 'student', '', '0/0', 'No'])

    def test_render_data_no_users(self):
        """
            Test get data without users
        """
        url = self.get_completion_data_url(self.course_no_user.id, 0)
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
        url = self.get_completion_data_url(self.course_empty.id, 0)
        self.response = self.super_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.super_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'], [[True]])
        self.assertEqual(data['completion'], [])

    def test_render_data_no_content_bigcourse(self):
        """
            Test get data without content
        """
        url = self.get_completion_data_url(self.course_no_content.id, 1)
        self.response = self.super_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.super_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(len(data['data']), 12)
        self.assertEqual(data['data'][-1][0], self.student.username)
        self.assertEqual(data['data'][-1][1], '')
        self.assertEqual(data['data'][-1][2], self.student.email)
        self.assertEqual(data['data'][-1][3], '')

    def test_render_data_no_users_bigcourse(self):
        """
            Test get data without users
        """
        url = self.get_completion_data_url(self.course_no_user.id, 1)
        self.response = self.super_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.super_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'], [[True]])

    def test_render_data_course_empty_bigcourse(self):
        """
            Test get data with empty course
        """
        url = self.get_completion_data_url(self.course_empty.id, 1)
        self.response = self.super_client.get(url)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'],[[False]])

        self.response = self.super_client.get(url)
        self.assertEqual(self.response.status_code, 200)
        data = json.loads(self.response.content.decode())
        self.assertEqual(data['data'], [[True]])
