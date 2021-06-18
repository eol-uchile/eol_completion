from django.conf import settings
from django.utils.translation import ugettext_noop

from lms.djangoapps.courseware.tabs import EnrolledTab
from xmodule.tabs import TabFragmentViewMixin
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.instructor import permissions


class EolCompletionTab(TabFragmentViewMixin, EnrolledTab):
    type = 'eol_completion'
    title = ugettext_noop('Seguimiento')
    priority = None
    view_name = 'completion_view'
    fragment_view_name = 'eol_completion.views.EolCompletionFragmentView'
    is_hideable = True
    is_default = True
    body_class = 'eol_completion'
    online_help_token = 'eol_completion'
    # True if this tab should be displayed only for instructors
    course_staff_only = True

    @classmethod
    def is_enabled(cls, course, user=None):
        """
        Returns true if the specified user has staff access.
        """
        data_researcher_access = False
        if user is not None:
            data_researcher_access = user.has_perm(permissions.CAN_RESEARCH, course.id)
        return bool(user and (has_access(user, 'staff', course, course.id) or data_researcher_access))
